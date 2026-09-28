// k6 load test for the zero-downtime rollout demonstration (spec 3.3): steady traffic through the
// Ingress while `kubectl set image` replaces every backend pod. Pass = zero failed requests.
//
// Runs *inside* the cluster, on purpose. A `kubectl port-forward` pins traffic to one pod, and that
// pod is exactly what a rollout replaces - the tunnel dies with it and the test measures the
// port-forward, not the rollout. Going through the Ingress -> Service -> endpoints path is what
// real users hit, and it is the path maxSurge/maxUnavailable, readiness and preStop protect.
//
//   kubectl -n civicpulse create configmap rollout-load --from-file=load/rollout-load.js
//   kubectl -n civicpulse run k6 --restart=Never --image=grafana/k6:0.54.0 \
//     --overrides='<mounts the configmap at /scripts>' -- run /scripts/rollout-load.js
//   kubectl -n civicpulse set image deployment/backend backend=<new image>   # while k6 runs
// Full command sequence and captured output: k8s/evidence/zero-downtime-rollout.txt
import http from "k6/http";
import { check } from "k6";

const TARGET = __ENV.TARGET || "http://traefik.kube-system.svc.cluster.local";
const HOST = __ENV.HOST_HEADER || "civicpulse.local";

export const options = {
  scenarios: {
    steady: {
      executor: "constant-vus",
      vus: 10,
      duration: __ENV.DURATION || "120s",
    },
  },
  thresholds: {
    http_req_failed: ["rate==0"],
  },
};

export default function () {
  const res = http.get(`${TARGET}/api/complaints?page=1&page_size=10`, {
    headers: { Host: HOST },
  });
  check(res, { "status is 200": (r) => r.status === 200 });
}
