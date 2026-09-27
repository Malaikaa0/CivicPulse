#!/usr/bin/env python3
"""Pre-submission lint (spec section 5.8): "It is a lint, not a grader. It catches the
mechanical failures behind most of section 5.3. A clean run does not guarantee a good mark;
a dirty run nearly guarantees a bad one."

Stdlib only, on purpose: this must run on a bare checkout before any dependency is installed.
Checks that need a file which does not exist yet (compose, k8s, workflows) report INFO and
skip, rather than failing on work that has not started.

Usage: python scripts/check_submission.py
Exit code: 0 if nothing FAILed (WARN and INFO do not affect it), 1 otherwise.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# ---- what an unpinned or forbidden image reference looks like -----------------------------

_LATEST_OR_UNTAGGED = re.compile(r"^[\w./-]+(:latest)?$")


def _is_pinned(image: str) -> bool:
    """True if image has a concrete tag or a digest, and that tag is not "latest"."""
    if "@sha256:" in image:
        return True
    if ":" not in image.rsplit("/", 1)[-1]:
        return False  # no tag at all -> defaults to :latest
    tag = image.rsplit(":", 1)[-1]
    return tag != "latest"


_SECRET_PATTERNS = {
    "Groq key": re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "OpenAI-style key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "PEM private key": re.compile(r"-----BEGIN (RSA|OPENSSH|EC|PRIVATE) KEY-----"),
}


@dataclass(frozen=True)
class Finding:
    level: str  # "FAIL" | "WARN" | "OK" | "INFO"
    title: str
    detail: str = ""


def _run_git(root: Path, *args: str) -> str:
    # Explicit UTF-8 with replacement: git log -p can contain any byte sequence (the seed data
    # alone has Urdu-influenced text), and the platform default encoding (cp1252 on Windows)
    # would otherwise crash this script rather than just lint the repository.
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout or ""


def _repo_root() -> Path:
    here = Path(__file__).resolve().parent
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=here,
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        return here.parent
    return Path(out.stdout.strip())


# ---- individual checks -----------------------------------------------------------------


def check_env_not_tracked(root: Path) -> Iterator[Finding]:
    tracked = _run_git(root, "ls-files").splitlines()
    leaked = [f for f in tracked if Path(f).name == ".env" or f.endswith("/.env")]
    if leaked:
        yield Finding("FAIL", ".env is tracked by git right now", ", ".join(leaked))
    else:
        yield Finding("OK", ".env is not tracked in the current checkout")


def check_env_never_in_history(root: Path) -> Iterator[Finding]:
    added = _run_git(
        root, "log", "--all", "--diff-filter=A", "--name-only", "--format="
    ).splitlines()
    hits = sorted({f for f in added if f and (Path(f).name == ".env" or f.endswith("/.env"))})
    if hits:
        yield Finding(
            "FAIL",
            ".env was committed at some point in git history (spec 5.3: -20, rotate the key)",
            ", ".join(hits),
        )
    else:
        yield Finding("OK", "no .env file found anywhere in git history")


def _masked(text: str) -> str:
    """Enough of the match to search for and recognise, not enough to be a leak of its own."""
    if len(text) <= 12:
        return text[:2] + "..."
    return f"{text[:6]}...{text[-4:]} ({len(text)} chars)"


def check_no_secret_looking_strings_in_history(root: Path) -> Iterator[Finding]:
    diff_text = _run_git(root, "log", "--all", "-p")
    any_hit = False
    for label, pattern in _SECRET_PATTERNS.items():
        matches = sorted({m.group(0) for m in pattern.finditer(diff_text)})
        for match in matches:
            any_hit = True
            # A heuristic regex match, not proof: test fixtures commonly use fake placeholders
            # in this exact shape (e.g. "AIza-gemini-secret-value"). WARN, not FAIL - a human
            # has to look at the masked snippet and decide, which is why it is shown at all.
            yield Finding(
                "WARN",
                f"a string matching {label} appears in git history - confirm it is not real",
                f'{_masked(match)}  ->  git log --all -p -S"{match[:12]}"',
            )
    if not any_hit:
        yield Finding("OK", "no known API-key patterns found anywhere in git history")


def check_gitignore(root: Path) -> Iterator[Finding]:
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        yield Finding("FAIL", ".gitignore is missing")
        return
    lines = {line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()}
    if ".env" in lines or any(line == ".env" for line in lines):
        yield Finding("OK", ".gitignore excludes .env")
    else:
        yield Finding("WARN", ".gitignore does not explicitly list .env")
    if not (root / ".env.example").exists():
        yield Finding("WARN", ".env.example is missing (spec wants it committed)")


def _dockerfiles(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("Dockerfile*")
        if ".git" not in p.parts and "node_modules" not in p.parts and ".venv" not in p.parts
    ]


def check_dockerfiles_pinned(root: Path) -> Iterator[Finding]:
    dockerfiles = _dockerfiles(root)
    if not dockerfiles:
        yield Finding("INFO", "no Dockerfile found yet")
        return
    any_bad = False
    for path in dockerfiles:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = re.match(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", line, re.IGNORECASE)
            if not match:
                continue
            image = match.group(1)
            if not _is_pinned(image):
                any_bad = True
                rel = path.relative_to(root)
                yield Finding(
                    "FAIL",
                    f"unpinned or :latest base image in {rel}:{lineno}",
                    line.strip(),
                )
    if not any_bad:
        yield Finding("OK", f"all FROM lines in {len(dockerfiles)} Dockerfile(s) are pinned")


def _compose_files(root: Path) -> list[Path]:
    return [p for p in (root / "compose.yaml", root / "compose.prod.yaml") if p.exists()]


def check_compose(root: Path) -> Iterator[Finding]:
    files = _compose_files(root)
    if not files:
        yield Finding("INFO", "no compose.yaml / compose.prod.yaml yet")
        return

    for path in files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root)

        for lineno, line in enumerate(text.splitlines(), start=1):
            match = re.match(r"^\s*image:\s*[\"']?([^\"'\s]+)", line)
            if match and not _is_pinned(match.group(1)):
                yield Finding("FAIL", f"unpinned or :latest image in {rel}:{lineno}", line.strip())

        if path.name == "compose.prod.yaml":
            if re.search(r"^\s*build:\s*", text, re.MULTILINE):
                yield Finding("FAIL", f"{rel} has a build: key (must be image: only)")
            else:
                yield Finding("OK", f"{rel} has no build: key")

            # A crude but effective heuristic: a `ports:` block inside a service whose name
            # looks like the database or the cache. False positives are possible (a comment,
            # an unrelated service containing "cache" in its name) - this is a lint, not a
            # grader, so it is reported as WARN, worth a human glance, not FAIL.
            for service_match in re.finditer(
                r"^\s{2}(\S+):\n((?:^\s{4,}.*\n?)*)", text, re.MULTILINE
            ):
                name, body = service_match.group(1), service_match.group(2)
                if re.search(r"postgres|redis|cache|db\b", name, re.IGNORECASE) and re.search(
                    r"^\s*ports:", body, re.MULTILINE
                ):
                    yield Finding(
                        "WARN",
                        f"{rel}: service '{name}' looks like it publishes a port",
                        "the database and cache must not be reachable from outside in prod",
                    )
    yield Finding("OK", f"checked {len(files)} compose file(s)")


def check_k8s_manifests(root: Path) -> Iterator[Finding]:
    k8s = root / "k8s"
    manifests = (
        [p for p in k8s.rglob("*.y*ml") if p.is_file() and p.stat().st_size > 0]
        if k8s.exists()
        else []
    )
    if not manifests:
        yield Finding("INFO", "no Kubernetes manifests yet")
        return

    for path in manifests:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root)
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = re.match(r"^\s*image:\s*[\"']?([^\"'\s]+)", line)
            if match and (":latest" in match.group(1) or _is_pinned(match.group(1)) is False):
                yield Finding("FAIL", f"unpinned or :latest image in {rel}:{lineno}", line.strip())
        if re.search(r"kind:\s*Deployment", text) and re.search(r"postgres", text, re.IGNORECASE):
            yield Finding(
                "FAIL",
                f"{rel}: PostgreSQL as a Deployment (spec: must be a StatefulSet with a PVC)",
            )
        if re.search(r"type:\s*(NodePort|LoadBalancer)", text) and re.search(
            r"postgres|redis", text, re.IGNORECASE
        ):
            yield Finding("FAIL", f"{rel}: the database or cache Service is NodePort/LoadBalancer")
    yield Finding("OK", f"checked {len(manifests)} Kubernetes manifest(s)")


def _workflow_files(root: Path) -> list[Path]:
    d = root / ".github" / "workflows"
    if not d.exists():
        return []
    return [p for p in d.glob("*.y*ml") if p.stat().st_size > 0]


def check_workflows(root: Path) -> Iterator[Finding]:
    workflows = _workflow_files(root)
    if not workflows:
        yield Finding("INFO", "no GitHub Actions workflows yet")
        return

    risky_name = re.compile(r"build|push|deploy|publish|release", re.IGNORECASE)
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(root)
        if not re.search(r"^permissions:", text, re.MULTILINE):
            yield Finding(
                "WARN", f"{rel}: no top-level permissions: block (default is broader than needed)"
            )
        # A crude per-job split: a line at exactly 2-space indent under "jobs:" starts a job.
        job_starts = list(re.finditer(r"^  (\S+):\s*$", text, re.MULTILINE))
        for i, job in enumerate(job_starts):
            job_id = job.group(1)
            start = job.end()
            end = job_starts[i + 1].start() if i + 1 < len(job_starts) else len(text)
            body = text[start:end]
            if risky_name.search(job_id) and not re.search(r"^\s*needs:", body, re.MULTILINE):
                yield Finding(
                    "FAIL",
                    f"{rel}: job '{job_id}' looks like it builds/pushes/deploys but has no needs:",
                )
        for lineno, line in enumerate(text.splitlines(), start=1):
            if re.search(r"uses:\s*\S+@(main|master)\b", line):
                yield Finding(
                    "WARN",
                    f"{rel}:{lineno}: action pinned to a branch, not a version/SHA",
                    line.strip(),
                )
    yield Finding("OK", f"checked {len(workflows)} workflow file(s)")


def check_readme(root: Path) -> Iterator[Finding]:
    readme = root / "README.md"
    if not readme.exists():
        yield Finding("INFO", "README.md does not exist yet")
        return
    text = readme.read_text(encoding="utf-8")
    if re.search(r"```", text) and re.search(r"docker compose up", text, re.IGNORECASE):
        yield Finding("OK", "README.md has a fenced quickstart command")
    else:
        yield Finding("WARN", "README.md exists but no obvious one-command quickstart was found")


def check_main_not_default_pushable(root: Path) -> Iterator[Finding]:
    """Best-effort: only runs if gh is installed and authenticated. Never fails the run."""
    try:
        remote = _run_git(root, "remote", "get-url", "origin").strip()
    except OSError:
        yield Finding("INFO", "could not read git remote; skipping branch-protection check")
        return
    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$", remote)
    if not match:
        yield Finding("INFO", "origin is not GitHub; skipping branch-protection check")
        return
    owner, repo = match.group(1), match.group(2)
    # The branch object carries `protected`; the /protection sub-resource does not - it 404s
    # instead of reporting false when nothing is configured, which this would otherwise miss.
    result = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/branches/main", "--jq", ".protected"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        yield Finding("INFO", "gh not available/authenticated; skipping branch-protection check")
        return
    if result.stdout.strip() == "true":
        yield Finding("OK", "main is protected on GitHub")
    else:
        yield Finding("FAIL", "main does NOT appear to be protected on GitHub")


CHECKS = (
    check_env_not_tracked,
    check_env_never_in_history,
    check_no_secret_looking_strings_in_history,
    check_gitignore,
    check_dockerfiles_pinned,
    check_compose,
    check_k8s_manifests,
    check_workflows,
    check_readme,
    check_main_not_default_pushable,
)


def run_all(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for check in CHECKS:
        findings.extend(check(root))
    return findings


def main() -> int:
    root = _repo_root()
    findings = run_all(root)

    order = {"FAIL": 0, "WARN": 1, "INFO": 2, "OK": 3}
    findings.sort(key=lambda f: order[f.level])

    for finding in findings:
        line = f"[{finding.level:>4}] {finding.title}"
        print(line)
        if finding.detail:
            print(f"       {finding.detail}")

    fails = sum(1 for f in findings if f.level == "FAIL")
    warns = sum(1 for f in findings if f.level == "WARN")
    print(f"\n{fails} FAIL, {warns} WARN, {len(findings) - fails - warns} OK/INFO")
    print("This is a lint, not a grader: a clean run does not guarantee a good mark.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
