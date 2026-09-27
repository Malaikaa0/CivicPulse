"""Tests for check_submission.py. Run with the backend venv:
    backend/.venv/Scripts/python.exe -m pytest scripts/test_check_submission.py

Not part of the backend's own suite (it lints the whole repo, not the app), so it is not counted
in backend coverage and does not need a database or Redis.
"""

import subprocess
from pathlib import Path

import pytest
from check_submission import (
    _is_pinned,
    _masked,
    check_compose,
    check_dockerfiles_pinned,
    check_env_never_in_history,
    check_env_not_tracked,
    check_workflows,
)

# ---- _is_pinned: the rule every other check depends on ----


@pytest.mark.parametrize(
    ("image", "pinned"),
    [
        ("postgres:16", True),
        ("python:3.12.10-slim-bookworm", True),
        ("myregistry.io/team/app:1.2.3", True),
        ("app@sha256:" + "a" * 64, True),
        ("postgres:latest", False),
        ("postgres", False),
        ("myregistry.io/team/app", False),  # a "/" before the (absent) tag must not confuse it
        ("python:3.12-slim", True),  # a real tag that merely isn't a full version is still pinned
    ],
)
def test_is_pinned(image: str, pinned: bool) -> None:
    assert _is_pinned(image) is pinned


def test_masked_never_reveals_the_whole_match() -> None:
    secret = "AIzaSomeRealLookingKeyValue123456"

    shown = _masked(secret)

    assert secret not in shown
    assert shown.startswith(secret[:6])


# ---- Dockerfile pinning, against real files ----


def test_flags_an_unpinned_dockerfile(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text('FROM python:latest\nCMD ["true"]\n')

    findings = list(check_dockerfiles_pinned(tmp_path))

    assert any(f.level == "FAIL" for f in findings)


def test_a_pinned_dockerfile_passes(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.12.10-slim-bookworm AS builder\nFROM python:3.12.10-slim-bookworm\n"
    )

    findings = list(check_dockerfiles_pinned(tmp_path))

    assert all(f.level != "FAIL" for f in findings)
    assert any(f.level == "OK" for f in findings)


def test_no_dockerfile_is_info_not_fail(tmp_path: Path) -> None:
    findings = list(check_dockerfiles_pinned(tmp_path))

    assert findings == [findings[0]]
    assert findings[0].level == "INFO"


# ---- compose.prod.yaml: no build:, no published db/cache port ----


def test_compose_prod_with_a_build_key_fails(tmp_path: Path) -> None:
    (tmp_path / "compose.prod.yaml").write_text(
        "services:\n  backend:\n    build: .\n    image: ghcr.io/x/backend:abc123\n"
    )

    findings = list(check_compose(tmp_path))

    assert any(f.level == "FAIL" and "build:" in f.title for f in findings)


def test_compose_prod_without_build_passes(tmp_path: Path) -> None:
    (tmp_path / "compose.prod.yaml").write_text(
        "services:\n  backend:\n    image: ghcr.io/x/backend:abc123\n"
    )

    findings = list(check_compose(tmp_path))

    assert not any(f.level == "FAIL" for f in findings)


def test_a_database_port_published_in_prod_is_flagged(tmp_path: Path) -> None:
    (tmp_path / "compose.prod.yaml").write_text(
        'services:\n  postgres:\n    image: postgres:16\n    ports:\n      - "5432:5432"\n'
    )

    findings = list(check_compose(tmp_path))

    assert any(f.level == "WARN" and "publishes a port" in f.title for f in findings)


def test_unpinned_image_in_compose_fails(tmp_path: Path) -> None:
    (tmp_path / "compose.yaml").write_text("services:\n  cache:\n    image: redis\n")

    findings = list(check_compose(tmp_path))

    assert any(f.level == "FAIL" for f in findings)


def test_no_compose_files_is_info(tmp_path: Path) -> None:
    findings = list(check_compose(tmp_path))

    assert findings == [findings[0]]
    assert findings[0].level == "INFO"


# ---- CI workflows: needs: on jobs that publish or deploy ----


def test_a_deploy_job_without_needs_fails(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "cd.yml").write_text(
        "permissions:\n  contents: read\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: []\n"
        "  deploy:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: []\n"
    )

    findings = list(check_workflows(tmp_path))

    assert any(f.level == "FAIL" and "deploy" in f.title for f in findings)


def test_a_deploy_job_with_needs_passes(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "cd.yml").write_text(
        "permissions:\n  contents: read\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: []\n"
        "  deploy:\n"
        "    needs: test\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: []\n"
    )

    findings = list(check_workflows(tmp_path))

    assert not any(f.level == "FAIL" for f in findings)


def test_missing_permissions_block_is_a_warning(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps: []\n")

    findings = list(check_workflows(tmp_path))

    assert any(f.level == "WARN" and "permissions" in f.title for f in findings)


def test_no_workflows_is_info(tmp_path: Path) -> None:
    findings = list(check_workflows(tmp_path))

    assert findings == [findings[0]]
    assert findings[0].level == "INFO"


# ---- .env: never tracked, never in history (a real, throwaway git repo) ----


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def scratch_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / "README.md").write_text("hello\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_a_clean_repo_has_no_env_findings(scratch_repo: Path) -> None:
    assert all(f.level == "OK" for f in check_env_not_tracked(scratch_repo))
    assert all(f.level == "OK" for f in check_env_never_in_history(scratch_repo))


def test_env_tracked_right_now_fails(scratch_repo: Path) -> None:
    (scratch_repo / ".env").write_text("SECRET=x\n")
    _git(scratch_repo, "add", "-f", ".env")
    _git(scratch_repo, "commit", "-q", "-m", "oops")

    assert any(f.level == "FAIL" for f in check_env_not_tracked(scratch_repo))


def test_env_committed_and_later_deleted_is_still_caught_in_history(scratch_repo: Path) -> None:
    (scratch_repo / ".env").write_text("SECRET=x\n")
    _git(scratch_repo, "add", "-f", ".env")
    _git(scratch_repo, "commit", "-q", "-m", "oops")
    (scratch_repo / ".env").unlink()
    _git(scratch_repo, "add", "-A")
    _git(scratch_repo, "commit", "-q", "-m", "remove it")

    # Deleting the file does not remove it from history: this is exactly why the spec's
    # deduction applies even after the mistake is "fixed" and the key must be rotated.
    assert all(f.level == "OK" for f in check_env_not_tracked(scratch_repo))  # gone now
    assert any(f.level == "FAIL" for f in check_env_never_in_history(scratch_repo))  # but was there
