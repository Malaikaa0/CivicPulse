# HPA and VPA load-test evidence

Captured against a real local k3d cluster (`civicpulse`, 1 server + 1 agent, metrics-server
pre-installed), running the `k8s/overlays/dev` manifests with images built from this repo's own
Dockerfiles and imported into the cluster - not a simulation.

- **`hpa-watch.txt`** - raw `kubectl -n civicpulse get hpa backend-hpa -w` output from a run
  on 2026-09-29, plus a follow-up `kubectl get hpa`/`kubectl get events` check once the watch had
  been left running. Shows `REPLICAS` rising from 2 to 3 as `cpu` utilization crosses the 60%
  target, then falling back to 2 once the default 5-minute scale-down stabilization window
  elapsed with CPU below target the whole time.
- **`k6-load-test-output.txt`** - raw output of the k6 load test that generated the load: the
  same `load/hpa-load.js` ramping-VUs scenario against `GET /api/complaints` (0 -> 40 VUs over
  30s, held at 40 VUs for 180s, ramped back to 0 over 30s). Final summary: 17326 requests, **0
  failed** (0.00%), avg latency 486ms, p95 748ms - well under the p95 3.49s seen in an earlier
  capture of the same test, so three replicas kept up comfortably rather than degrading.
- **`hpa-replicas-vs-load.svg`** - replicas vs. offered load (VUs) over time, regenerated from
  `hpa-watch.txt`. The blue replicas line and its markers are real observations; the green
  offered-load trapezoid is the k6 script's known ramp design (0/30/210/240s), not a per-second
  capture, since this run's k6 output only kept the final summary, not the progress log. The
  x-axis assumes a 15s HPA poll interval (the actual watch output doesn't carry per-line
  timestamps); the final "back to 2 replicas" point is real but was confirmed separately, about
  9.5 minutes after the run started - see the note on the chart. Open it in a browser or image
  viewer.

See [`docs/ENGINEERING-NOTES.md`](../../docs/ENGINEERING-NOTES.md) question 5 for the
lag analysis these logs support.

## VPA (recommender mode)

- **`vpa-recommendation.txt`** - `kubectl describe vpa backend-vpa` output captured during the
  load test above: `Target: cpu: 587m` against a guessed `250m` request.
- **`hpa-watch-after-vpa-update.txt`** / **`k6-load-test-output-after-vpa-update.txt`** - a load
  test re-run after raising the request to `600m` (and the limit from `500m` to `1`). Peak HPA
  utilization went from 101% (4 replicas) to 84% (3 replicas), but the runs were not identical:
  the second was shorter and, with CPU throttling gone, served ~1.9x the throughput (30.8 vs 16.2
  req/s). So each pod did about twice the work before the HPA reacted; the request change and the
  limit change can't be separated from this pair of runs.

See question 6 in `docs/ENGINEERING-NOTES.md` for the full loop, what these runs do and don't
show, and why VPA stays in Off mode alongside the HPA.

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
