# CivicPulse frontend

React 18 + Vite + TypeScript, served by nginx from a multi-stage image.

## Development

```
npm install
npm run dev
```

`npm run dev` talks directly to `http://localhost:5173`; API calls (`/api/...`) need a backend
reachable at that same origin, or use `docker compose up` once `compose.yaml` exists.

## Runtime configuration (ADR-0002)

The frontend never has a backend URL baked into it. `nginx.conf` proxies `/api/*` to the
`backend` service, so the exact same built image runs unchanged in dev, CI and Kubernetes -
only the network the container joins changes which `backend` it reaches.

## Checks

```
npm run lint
npm run build   # tsc -b && vite build
npm test        # vitest run
```

## Docker

```
docker build -t civicpulse-frontend .
```

Multi-stage: `node:22-alpine` builds the static bundle, `nginx:1.27-alpine` serves it. The final
image is ~74MB, matching the bare `nginx:1.27-alpine` base almost exactly - no Node toolchain or
source makes it into the runtime layer.
