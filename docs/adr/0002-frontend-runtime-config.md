# ADR-0002: Frontend runtime configuration

- **Status:** Accepted
- **Owner:** M2 (frontend / DevOps)

## Context
Vite bakes `import.meta.env` values into the static bundle at build time. A baked-in API URL
makes the image environment-specific and breaks build-once-deploy-many.

## Decision
Proxy `/api` through nginx to the backend, so the frontend only ever uses relative URLs and
needs no absolute backend address. The alternative, generating `/config.js` from environment
variables at container start, was rejected as one more moving part.

## Consequences
Implemented in [`frontend/nginx.conf`](../../frontend/nginx.conf): `location /api/` forwards to
a `$backend` variable resolved lazily per request (`resolver 127.0.0.11 valid=10s`), never a bare
`proxy_pass http://backend:8000`, because the latter resolves DNS once at nginx startup and
refuses to start at all if that lookup fails - a real risk here since nothing guarantees the
backend's DNS entry exists before the frontend container does.

**Good:** the exact same built image runs unchanged against Compose's `backend` service and
Kubernetes' `backend` Service - confirmed by running it against both without a rebuild. One image,
promoted through environments, never rebuilt per-environment, is the whole point of the ADR.

**Learned the hard way:** the naive version of this (`proxy_pass http://backend:8000` directly)
was tried first and failed exactly as predicted - nginx wouldn't start if `backend` wasn't
resolvable yet, turning a slow dependency into a hard startup failure instead of a transient 502
on one request. The `resolver` + variable form was the fix, and a second mistake followed
immediately: using a variable in `proxy_pass` also disables nginx's normal path-prefix rewriting,
so an early version appended a path after the variable and silently mangled every forwarded URL.
The final form passes the request URI through unchanged, matching that the backend's own routes
already carry the `/api` prefix.

**Cost:** one extra layer of indirection (the `resolver`/variable pattern) that a reader has to
understand before the proxy block makes sense - worth a comment in `nginx.conf` itself, which it
has.

**Revisit when:** the frontend needs a piece of *build-time* configuration that legitimately
differs per environment and isn't reachable through the API (e.g. a feature flag). At that point
this ADR's scope (routing only) still holds, but a `/config.js` generated at container start
would need reconsidering rather than rejecting outright.
