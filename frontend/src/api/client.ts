/**
 * API client. Every request is relative ("/api/...") on purpose: nginx proxies /api to the
 * backend (see nginx.conf and ADR-0002), so the frontend never needs an absolute backend URL
 * baked in at build time - the same built image runs against Compose's or Kubernetes' backend
 * Service unchanged.
 */

export type Category = "water" | "electricity" | "sanitation" | "roads" | "streetlights" | "other";
export type Priority = "high" | "normal" | "low";
export type Status = "open" | "in_progress" | "resolved" | "rejected";

export interface Stats {
  total: number;
  by_category: Record<string, number>;
  by_priority: Record<string, number>;
  by_status: Record<string, number>;
}

export interface StatsResult {
  stats: Stats;
  cacheStatus: string | null; // the X-Cache header: "HIT" | "MISS" | null if absent
}

export interface Complaint {
  id: string;
  text: string;
  location: string;
  reporter_contact: string | null;
  category: Category;
  priority: Priority;
  status: Status;
  ai_summary: string | null;
  triaged_by: string;
  triage_latency_ms: number;
  created_at: string;
  updated_at: string;
  allowed_transitions: Status[];
}

export interface ComplaintPage {
  items: Complaint[];
  total: number;
  page: number;
  page_size: number;
}

export interface FieldError {
  field: string;
  message: string;
}

/** Thrown for any non-2xx response. Callers pattern-match on `kind` to render the right message. */
export class ApiError extends Error {
  readonly status: number;

  constructor(
    message: string,
    status: number,
    public readonly kind: "validation" | "not_found" | "conflict" | "rate_limited" | "unknown",
    public readonly fieldErrors?: FieldError[],
    public readonly current?: Status,
    public readonly requested?: Status,
    public readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.status = status;
  }
}

async function parseErrorAndThrow(response: Response): Promise<never> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (response.status === 400 && body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (Array.isArray(detail)) {
      throw new ApiError("Validation failed", 400, "validation", detail as FieldError[]);
    }
  }
  if (response.status === 404) {
    throw new ApiError("Not found", 404, "not_found");
  }
  if (response.status === 409 && body && typeof body === "object" && "detail" in body) {
    const b = body as { detail: string; current?: Status; requested?: Status };
    // The server's own message, surfaced verbatim - never a client-side paraphrase of it.
    throw new ApiError(b.detail, 409, "conflict", undefined, b.current, b.requested);
  }
  if (response.status === 429) {
    // `Number(x) || undefined` would drop a legitimate "0" (retry immediately); only a missing,
    // blank or non-numeric header means "unknown".
    const header = response.headers.get("Retry-After");
    const parsed = header === null || header.trim() === "" ? NaN : Number(header);
    const retryAfter = Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : "Rate limit exceeded";
    throw new ApiError(detail, 429, "rate_limited", undefined, undefined, undefined, retryAfter);
  }
  throw new ApiError(`Request failed: ${response.status}`, response.status, "unknown");
}

export interface SubmitComplaintInput {
  text: string;
  location: string;
  reporter_contact?: string;
}

export async function submitComplaint(input: SubmitComplaintInput): Promise<Complaint> {
  const response = await fetch("/api/complaints", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) return parseErrorAndThrow(response);
  return (await response.json()) as Complaint;
}

export interface ListComplaintsParams {
  page?: number;
  page_size?: number;
  category?: Category;
  priority?: Priority;
  status?: Status;
}

export async function listComplaints(params: ListComplaintsParams = {}): Promise<ComplaintPage> {
  const query = new URLSearchParams();
  if (params.page) query.set("page", String(params.page));
  if (params.page_size) query.set("page_size", String(params.page_size));
  if (params.category) query.set("category", params.category);
  if (params.priority) query.set("priority", params.priority);
  if (params.status) query.set("status", params.status);

  const qs = query.toString();
  const response = await fetch(`/api/complaints${qs ? `?${qs}` : ""}`);
  if (!response.ok) return parseErrorAndThrow(response);
  return (await response.json()) as ComplaintPage;
}

export async function getComplaint(id: string): Promise<Complaint> {
  const response = await fetch(`/api/complaints/${id}`);
  if (!response.ok) return parseErrorAndThrow(response);
  return (await response.json()) as Complaint;
}

export async function changeComplaintStatus(id: string, status: Status): Promise<Complaint> {
  const response = await fetch(`/api/complaints/${id}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!response.ok) return parseErrorAndThrow(response);
  return (await response.json()) as Complaint;
}

export async function getStats(): Promise<StatsResult> {
  const response = await fetch("/api/stats");
  if (!response.ok) return parseErrorAndThrow(response);
  const stats = (await response.json()) as Stats;
  return { stats, cacheStatus: response.headers.get("X-Cache") };
}
