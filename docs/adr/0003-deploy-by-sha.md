# ADR-0003: Deploy by commit SHA

- **Status:** Proposed
- **Owner:** M2 (frontend / DevOps)

## Context
"What is production running?" must have a one-word answer that can be pasted into
`git show`. A mutable tag such as `latest` cannot give that answer.

## Decision
Images are pushed to GHCR tagged with `${{ github.sha }}` (and `latest`, which is pushed but
never deployed). Deployments reference the SHA. Deploying by image digest is a bonus goal.

## Consequences
TBD - to be written in week 4, after the decision has been lived with.
