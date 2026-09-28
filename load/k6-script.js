// Entry point matching the path the spec names (§5.7: load/k6-script.js).
//
// This repo has two purpose-built load scripts rather than one generic one, because they test
// different things and are documented separately in k8s/evidence/ under their own names:
//   - hpa-load.js:     ramps to 40 VUs against the backend Service, used for the HPA scale-out
//                       evidence (k8s/evidence/hpa-watch.txt, hpa-replicas-vs-load.svg) and the
//                       VPA before/after comparison.
//   - rollout-load.js: steady load through the Ingress, used for the zero-downtime rollout
//                       evidence (k8s/evidence/zero-downtime-rollout.txt).
// This file re-exports hpa-load.js (the one referenced by §3.3's HPA deliverable) so
// `k6 run load/k6-script.js` also works, without duplicating either script or renaming the files
// the evidence in k8s/evidence/ already cites by name.
export { default, options } from "./hpa-load.js";
