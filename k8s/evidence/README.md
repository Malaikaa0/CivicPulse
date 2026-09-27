# HPA and VPA load-test evidence

Captured against a real local k3d cluster (`civicpulse`, 1 server + 1 agent, metrics-server
pre-installed), running the `k8s/overlays/dev` manifests with images built from this repo's own
Dockerfiles and imported into the cluster - not a simulation.

- **`hpa-watch.txt`** - raw `kubectl -n civicpulse get hpa backend-hpa -w` output for the
  duration of the load test below. Shows `REPLICAS` rising from 2 to 4 as `cpu` utilization
  crosses the 60% target, and falling CPU% once the extra replicas spread the load.
- **`k6-load-test-output.txt`** - raw output of the k6 load test that generated the load: a
  ramping-VUs scenario against `GET /api/complaints` (0 -> 40 VUs over 30s, held at 40 VUs for
  180s, ramped back to 0 over 30s). Final summary: 3900 requests, **0 failed** (0.00%), p95
  latency 3.49s at the peak of the burst (baseline is well under 1s) - the two original replicas
  degraded gracefully rather than dropping requests while the HPA reacted.

See [`docs/ENGINEERING-NOTES.md`](../../docs/ENGINEERING-NOTES.md) question 5 for the
lag analysis these logs support.

## VPA (recommender mode)

- **`vpa-recommendation.txt`** - `kubectl describe vpa backend-vpa` output captured during the
  load test above: `Target: cpu: 587m` against a guessed `250m` request.
- **`hpa-watch-after-vpa-update.txt`** / **`k6-load-test-output-after-vpa-update.txt`** - the same
  load test re-run after updating `backend.yaml`'s request to match the VPA recommendation
  (`600m`). Peak reported HPA utilization dropped from 101% (4 replicas) to 84% (3 replicas) for
  the same offered load - the request denominator changed, not the real CPU usage.

See question 6 in `docs/ENGINEERING-NOTES.md` for the full record→test→describe→update→retest
loop and why this is exactly the HPA/VPA conflict the spec warns about.
