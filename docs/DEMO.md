# Demo video script

Target length: **4:50**, within the 5-minute limit. The rubric (spec section 4, J) asks the video
to cover: clean clone -> running system, AI triage, fallback, network isolation failing, HPA
scaling, and rollback, with **both partners speaking**. Each of those has its own segment below.

- **M1** = backend / AI / data / cache.
- **M2** = frontend / Docker Compose / Kubernetes / CI/CD.

All commands are bash (Git Bash on Windows works) and run from the repository root. They come
from `README.md`, `docs/RUNBOOK.md`, `compose.yaml` and `k8s/`.

---

## Before recording (off camera)

These steps are slow or involve secrets, so they are not recorded.

1. **Docker Desktop running**, with the base images already pulled so the on-camera build is
   short: `docker pull postgres:16 redis:7 python:3.12.10-slim-bookworm node:22.11-alpine nginx:1.27-alpine`.
2. **A real Gemini key** ready to paste into `.env` for segment 2. Do not show it on screen:
   edit `.env` off camera, or blur that part of the recording.
3. **Local k3d cluster with the dev overlay deployed**, for segments 5 and 6. `k8s/overlays/dev`
   expects locally built images tagged `:local` with `imagePullPolicy: Never`:

   ```bash
   k3d cluster create civicpulse --agents 1 -p "8080:80@loadbalancer" --wait
   docker build -t civicpulse-backend:local ./backend
   docker build -t civicpulse-frontend:local ./frontend
   k3d image import civicpulse-backend:local civicpulse-frontend:local -c civicpulse
   kubectl apply -k k8s/overlays/dev
   kubectl -n civicpulse wait --for=condition=complete job/migrate --timeout=180s
   kubectl -n civicpulse rollout status deployment/backend
   kubectl -n civicpulse rollout status deployment/frontend
   curl -s -H "Host: civicpulse.local" http://localhost:8080/api/stats   # sanity check
   ```

   k3s ships metrics-server, which the HPA needs. Check that `kubectl -n civicpulse get hpa`
   shows a real CPU percentage, not `<unknown>`, before recording.
4. **Use a fresh directory for segment 1** (for example `~/demo`), so the clone really is clean.
5. **Terminal layout:** one large terminal, font size 18 or more. Segment 5 needs two panes side by
   side.

---

## Shot list

### 0:00 - 0:20 · Introduction (both)

| Who | Says | Screen |
|---|---|---|
| M1 | "This is CivicPulse. Citizens send free-text complaints to their city, and an LLM sorts each one by category and priority, so a burst water main doesn't wait behind a faded road marking." | README on GitHub, architecture diagram visible |
| M2 | "I built the frontend, Compose, Kubernetes and the pipeline. M1 built the backend and the AI layer. We'll start from a clean clone." | Same |

### 0:20 - 1:05 · Clean clone to running system (M2)

```bash
git clone https://github.com/Malaikaa0/CivicPulse.git && cd CivicPulse
cp .env.example .env
docker compose up -d --build
```

- **Cut** while the build runs. Keep the last few lines, where the containers turn healthy.

```bash
docker compose ps
curl -s http://localhost:3000/api/stats
```

- M2: "One command. Postgres and Redis start first, then the migrate job, then the seed job with
  32 complaints, then the backend and frontend. `ps` shows `migrate` and `seed` exited
  successfully: they are one-shot jobs."
- **Screen:** open `http://localhost:3000` in the browser. Show the Dashboard with the seeded
  complaints, then the Stats view.

### 1:05 - 1:55 · AI triage (M1)

`.env` now has a real `GEMINI_API_KEY` (added off camera). Switch the backend to the hosted
provider:

```bash
TRIAGE_PROVIDER=llm docker compose up -d backend
```

- M1: "Same image. Only the environment changed."
- **Screen:** in the browser's Submit view, send:
  *"Sewage is overflowing from an open manhole outside the school gate, children walk past it
  every morning."*, location *"Block 4, Gulberg"*. Show the result card with category, priority
  and summary.
- Then in the terminal:

```bash
curl -s -X POST http://localhost:8000/api/complaints -H "Content-Type: application/json" \
  -d '{"text":"The water pipe on Canal Road burst and the street is flooding.","location":"Canal Road"}'
curl -s http://localhost:8000/api/meta/providers
```

- M1 points at `"triaged_by": "llm:gemini"` and `triage_latency_ms` in the first response.
- M1: "The model's answer is never trusted as it is. It has to validate against our Pydantic
  schema: the enum, the 140-character summary, confidence between 0 and 1. Only redacted text
  is sent, never the location or the reporter's contact."
- Run the same `curl -X POST` again, then `/api/meta/providers` again. Point at `cache.hits`
  going up. M1: "A duplicate complaint is served from the content-hash cache in Redis: one
  inference, not two."

### 1:55 - 2:35 · Fallback (M1)

Break the provider on purpose with an invalid key:

```bash
GEMINI_API_KEY=invalid-key TRIAGE_PROVIDER=llm docker compose up -d backend
curl -s -i -X POST http://localhost:8000/api/complaints -H "Content-Type: application/json" \
  -d '{"text":"Streetlight outside house 12 has been off for a week.","location":"Model Town"}'
```

- **Screen:** `HTTP/1.1 201 Created` and `"triaged_by": "rules:fallback"`.
- M1: "The provider rejected the call, and the citizen still got a 201. The deterministic
  rule-based provider stepped in. Timeouts, 429s and 5xx get one jittered retry first. A 400
  never gets retried."

```bash
docker compose logs backend | grep '"level":"WARNING"'
curl -s http://localhost:8000/api/meta/providers
```

- Point at the one WARNING line (`complaint_id`, `provider`, `error_class`) and at
  `"fallback": true` in `recent_outcomes`. M1: "The fallback works so well that nobody would
  notice. That's why the fallback is logged and shown on this endpoint."
- Restore the backend before moving on (can be cut):

```bash
docker compose up -d backend
```

### 2:35 - 3:05 · Network isolation failing (M2)

```bash
docker compose exec frontend ping -c 1 postgres
docker compose exec frontend wget -qO- http://backend:8000/health
```

- **Screen:** the `ping` fails (busybox reports `bad address 'postgres'`: the name doesn't even resolve from the frontend's network). The `wget` returns the health JSON.
- M2: "The frontend is on the `edge` network only. Postgres and Redis are on `internal`, which is
  `internal: true`, so it has no route out at all. The backend is the only service on both
  networks, because it's the only one that needs the database and the internet."
- **Screen:** briefly show the `networks:` lines in `compose.yaml`: `backend: [edge, internal]`,
  `frontend: [edge]`, `postgres`/`redis: [internal]`.

### 3:05 - 4:05 · HPA scaling (M2)

Switch to the k3d cluster prepared off camera. Two panes.

**Left pane:**

```bash
kubectl -n civicpulse get hpa backend-hpa -w
```

**Right pane:** generate load inside the cluster against the backend Service:

```bash
kubectl -n civicpulse run load --image=busybox:1.36 --restart=Never -- /bin/sh -c \
  'for i in 1 2 3 4 5 6 7 8; do (while true; do wget -q -O /dev/null http://backend:8000/api/complaints; done) & done; wait'
```

- M2: "The HPA targets 60% CPU, with 2 to 10 replicas. It scales up immediately and scales down
  only after five minutes, so a bursty load doesn't make it flap."
- **Cut** the wait. Show `TARGETS` going over 60% and `REPLICAS` rising from 2. Then:

```bash
kubectl -n civicpulse get pods -l app=backend
kubectl -n civicpulse delete pod load
```

- M2: "We measured this with k6 as well: about 15 to 20 seconds from full load to a scaling
  decision, and zero failed requests out of 3900."
- **Screen:** `k8s/evidence/hpa-replicas-vs-load.svg` for 3 seconds.

### 4:05 - 4:45 · Rollback (M1)

Ship a bad release, then roll it back both ways.

```bash
kubectl -n civicpulse set image deployment/backend backend=civicpulse-backend:does-not-exist
kubectl -n civicpulse get pods -l app=backend
```

- **Screen:** the new pod stuck on `ErrImageNeverPull`, and the old pods still `Running`.
- M1: "`maxUnavailable` is 0, so the old pods keep serving while the new one fails."

```bash
kubectl -n civicpulse rollout undo deployment/backend
kubectl -n civicpulse rollout status deployment/backend
```

- M1: "`rollout undo` is the 3 a.m. answer: fast and imperative. But the cluster's history isn't
  the record of truth. Git is. So once the fire is out, we re-apply the overlay we know is good."

```bash
kubectl apply -k k8s/overlays/dev
kubectl -n civicpulse rollout status deployment/backend
```

- M1: "In production, that means setting `overlays/prod` back to the previous commit SHA with
  `kustomize edit set image` and applying it. That's the declarative rollback in the runbook. We
  never deploy `latest`, so `git show` on the running SHA tells you exactly what's in
  production."

### 4:45 - 4:50 · Close (both)

| Who | Says |
|---|---|
| M2 | "Every merge to main is tested again, pushed to GHCR by commit SHA and deployed to a throwaway cluster by the CD pipeline." |
| M1 | "And when the AI fails, the citizen never notices. The operator does. Thanks for watching." |

---

## Checklist before publishing

- [ ] Total length is 5:00 or less.
- [ ] Both partners can be heard.
- [ ] All six rubric items are visible: clean clone -> running, AI triage (`llm:gemini`),
      fallback (`rules:fallback` with a 201), failed `ping postgres`, HPA replicas rising,
      `rollout undo` plus a declarative re-apply.
- [ ] No API key, password or `.env` content is visible in any frame.
- [ ] The video is uploaded as unlisted, and the link is added to the submission (spec 5.8).
