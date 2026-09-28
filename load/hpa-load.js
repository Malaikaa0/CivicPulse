// k6 load test used for the HPA evidence in k8s/evidence/ (hpa-watch.txt, k6-load-test-output.txt,
// hpa-replicas-vs-load.svg) and for the VPA before/after comparison.
//
// Ramps to 40 virtual users, holds for three minutes, ramps down. Targets the list endpoint because
// it is not rate-limited (only POST /api/complaints is) and does real database work per request.
//
// Run against a port-forwarded backend:
//   kubectl -n civicpulse port-forward svc/backend 18000:8000
//   k6 run load/hpa-load.js
// or point it elsewhere:
//   BASE_URL=http://civicpulse.local k6 run load/hpa-load.js
// and in another terminal:
//   kubectl -n civicpulse get hpa backend-hpa -w
import http from "k6/http";
import { check } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:18000";

export const options = {
  scenarios: {
    ramp: {
      executor: "ramping-vus",
      startVUs: 1,
      stages: [
        { duration: "30s", target: 40 },
        { duration: "180s", target: 40 },
        { duration: "30s", target: 0 },
      ],
    },
  },
  thresholds: {
    // The scale-out run finished with 0 of 3900 requests failed; hold future runs to that.
    http_req_failed: ["rate==0"],
  },
};

export default function () {
  const res = http.get(`${BASE_URL}/api/complaints?page=1&page_size=50`);
  check(res, { "status is 200": (r) => r.status === 200 });
}
