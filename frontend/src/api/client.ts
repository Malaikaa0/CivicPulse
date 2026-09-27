/**
 * Minimal API client. Requests are relative ("/api/...") on purpose: nginx proxies /api to the
 * backend (see nginx.conf and ADR-0002), so the frontend never needs an absolute backend URL
 * baked in at build time. A typed client generated from the backend's OpenAPI schema
 * (GET /openapi.json) replaces this once the real views land.
 */

export interface Stats {
  total: number;
  by_category: Record<string, number>;
  by_priority: Record<string, number>;
  by_status: Record<string, number>;
}

export async function getStats(): Promise<Stats> {
  const response = await fetch("/api/stats");
  if (!response.ok) {
    throw new Error(`GET /api/stats failed: ${response.status}`);
  }
  return (await response.json()) as Stats;
}
