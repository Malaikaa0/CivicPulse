# ADR-0003: Deploy by commit SHA

- **Status:** Accepted
- **Owner:** M2 (frontend / DevOps)

## Context
"What is production running?" must have a one-word answer that can be pasted into
`git show`. A mutable tag such as `latest` cannot give that answer.

## Decision
Images are pushed to GHCR tagged with `${{ github.sha }}` (and `latest`, which is pushed but
never deployed). Deployments reference the SHA. Deploying by image digest is a bonus goal.

## Consequences
Implemented in `compose.prod.yaml` (`image: ghcr.io/malaikaa0/civicpulse-backend:${IMAGE_TAG}`,
no `build:` key anywhere in the file - a deploy can only ever run an image that was already built
and pushed, never one assembled on the deploying machine) and, on Kubernetes,
`k8s/overlays/prod/kustomization.yaml`'s `images:` transformer, meant to be repointed by the CD
job (`kustomize edit set image ...=...:$SHA`) immediately before every apply.

**Good:** rollback became a one-line, auditable operation instead of a guess - `IMAGE_TAG=<sha>
docker compose -f compose.prod.yaml up -d` (or the Kubernetes equivalent in
`docs/RUNBOOK.md`) with `git show <sha>` as the answer to "what, exactly, is this." No `docker
compose pull && restart` ambiguity about which build of `latest` happens to be cached where.

**Learned the hard way:** `latest` still gets pushed on every build (a `docker pull
.../civicpulse-backend:latest` convenience for local poking-around), and that tag existing at all
is a standing temptation to `docker compose up -d` without setting `IMAGE_TAG` first and
silently deploy whatever `latest` happens to point at. The `.env`/CI variable is the only guard
against that - there's no code-level enforcement stopping someone from typing the wrong command.

**Cost:** a human (or a CI job) must set `IMAGE_TAG` correctly on every deploy; there is no
fallback that "just works" by default, on purpose - see ADR context, the whole point is refusing
to answer "what's running" with a shrug.

**Revisit when:** the bonus goal (deploying by image digest, `@sha256:...`, rather than a tag)
gets built - a digest is immutable in a way a SHA tag technically isn't (a tag can, in principle,
be force-pushed to point at a different image; a digest cannot), which closes the one remaining
gap in "what is production running" being a fully tamper-evident answer.
