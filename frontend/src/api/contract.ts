/**
 * The shape of the backend contract as the frontend understands it, in a form both the compiler
 * and a test can check.
 *
 * `satisfies Record<keyof X, true>` makes each object list exactly the keys of the matching type in
 * client.ts - a missing or extra key is a compile error. tests/apiContract.test.ts then compares
 * these same keys against the backend's OpenAPI schema (src/api/openapi.json, kept in sync with
 * the live API by backend/tests/test_openapi_snapshot.py). Together: client.ts types == this file
 * == the real API.
 */
import type { Category, Complaint, ComplaintPage, Priority, Stats, Status } from "./client";

export const COMPLAINT_FIELDS = {
  id: true,
  text: true,
  location: true,
  reporter_contact: true,
  category: true,
  priority: true,
  status: true,
  ai_summary: true,
  triaged_by: true,
  triage_latency_ms: true,
  created_at: true,
  updated_at: true,
  allowed_transitions: true,
} satisfies Record<keyof Complaint, true>;

export const COMPLAINT_PAGE_FIELDS = {
  items: true,
  total: true,
  page: true,
  page_size: true,
} satisfies Record<keyof ComplaintPage, true>;

export const STATS_FIELDS = {
  total: true,
  by_category: true,
  by_priority: true,
  by_status: true,
} satisfies Record<keyof Stats, true>;

export const CATEGORIES = {
  water: true,
  electricity: true,
  sanitation: true,
  roads: true,
  streetlights: true,
  other: true,
} satisfies Record<Category, true>;

export const PRIORITIES = { high: true, normal: true, low: true } satisfies Record<Priority, true>;

export const STATUSES = {
  open: true,
  in_progress: true,
  resolved: true,
  rejected: true,
} satisfies Record<Status, true>;
