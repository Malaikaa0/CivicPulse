# ADR-0002: Frontend runtime configuration

- **Status:** Proposed
- **Owner:** M2 (frontend / DevOps)

## Context
Vite bakes `import.meta.env` values into the static bundle at build time. A baked-in API URL
makes the image environment-specific and breaks build-once-deploy-many.

## Decision
Proxy `/api` through nginx to the backend, so the frontend only ever uses relative URLs and
needs no absolute backend address. The alternative, generating `/config.js` from environment
variables at container start, was rejected as one more moving part.

## Consequences
TBD - to be written in week 4, after the decision has been lived with.
