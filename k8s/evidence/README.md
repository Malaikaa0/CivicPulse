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
- **`hpa-replicas-vs-load.svg`** - replicas vs. offered load (VUs) over time, plotted directly
  from the two files above (every data point in it is a real observation, not illustrative). CPU%
  is included as a thin reference line. Open it in a browser or image viewer.

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

## A note on the raw k6 output

k6 prints the local filesystem path of the script it was given at the top of its own output.
The scripts now live in `load/` (`hpa-load.js`, `rollout-load.js`); at the time of these runs
they were saved in a local temp/tooling directory, and the path k6 printed pointed there - redacted in the two `k6-load-test-output*.txt` files above
since it's an artifact of where the script happened to be saved, not something that says anything
about the test or its result. Every number in these files (request counts, latencies, failure
rate, VU counts) is untouched and exactly as k6 reported it.

## Zero-downtime rollout and rollback

- **`zero-downtime-rollout.txt`** - `kubectl set image` replaced both backend pods while
  `load/rollout-load.js` sent 10 VUs of traffic through the Ingress for 120s, from inside the
  cluster. **0 of 19087 requests failed.** Max latency was 28s (p95 217ms), recorded as-is: most
  likely one request in flight on a draining pod.
- **`rollback.txt`** - `kubectl rollout undo` straight after, taking all 10 replicas (the HPA
  had scaled up under the load) back to the previous image in one command. The declarative
  alternative, re-applying the previous SHA, is in `docs/RUNBOOK.md`.
