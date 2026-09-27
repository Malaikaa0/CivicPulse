import { useCallback, useEffect, useState } from "react";
import type { Category, Complaint, ListComplaintsParams, Priority, Status } from "../api/client";
import { ApiError, changeComplaintStatus, listComplaints } from "../api/client";

const PAGE_SIZE = 10;

const CATEGORY_OPTIONS: Category[] = [
  "water",
  "electricity",
  "sanitation",
  "roads",
  "streetlights",
  "other",
];
const PRIORITY_OPTIONS: Priority[] = ["high", "normal", "low"];
const STATUS_OPTIONS: Status[] = ["open", "in_progress", "resolved", "rejected"];

interface Filters {
  category: Category | "";
  priority: Priority | "";
  status: Status | "";
}

const EMPTY_FILTERS: Filters = { category: "", priority: "", status: "" };

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready" };

function formatLabel(value: string): string {
  return value.replace(/_/g, " ");
}

function snippet(text: string, maxLength = 140): string {
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength).trimEnd()}…`;
}

function DashboardPage() {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [complaints, setComplaints] = useState<Complaint[]>([]);
  const [total, setTotal] = useState(0);
  const [loadState, setLoadState] = useState<LoadState>({ status: "loading" });
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
  const [transitionErrors, setTransitionErrors] = useState<Record<string, string>>({});

  const fetchPage = useCallback((targetPage: number, targetFilters: Filters) => {
    setLoadState({ status: "loading" });

    const params: ListComplaintsParams = { page: targetPage, page_size: PAGE_SIZE };
    if (targetFilters.category) params.category = targetFilters.category;
    if (targetFilters.priority) params.priority = targetFilters.priority;
    if (targetFilters.status) params.status = targetFilters.status;

    let cancelled = false;

    listComplaints(params)
      .then((result) => {
        if (cancelled) return;
        setComplaints(result.items);
        setTotal(result.total);
        setLoadState({ status: "ready" });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoadState({
          status: "error",
          message: error instanceof Error ? error.message : "Failed to load complaints.",
        });
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return fetchPage(page, filters);
  }, [page, filters, fetchPage]);

  function updateFilter<K extends keyof Filters>(key: K, value: Filters[K]) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(1);
  }

  function handleTransition(complaint: Complaint, nextStatus: Status) {
    setPendingIds((prev) => {
      const next = new Set(prev);
      next.add(complaint.id);
      return next;
    });
    setTransitionErrors((prev) => {
      if (!(complaint.id in prev)) return prev;
      const next = { ...prev };
      delete next[complaint.id];
      return next;
    });

    changeComplaintStatus(complaint.id, nextStatus)
      .then((updated) => {
        setComplaints((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
      })
      .catch((error: unknown) => {
        const message =
          error instanceof ApiError && error.kind === "conflict"
            ? error.message
            : error instanceof Error
              ? error.message
              : "Failed to update status.";
        setTransitionErrors((prev) => ({ ...prev, [complaint.id]: message }));
      })
      .finally(() => {
        setPendingIds((prev) => {
          const next = new Set(prev);
          next.delete(complaint.id);
          return next;
        });
      });
  }

  const isLoading = loadState.status === "loading";
  const isError = loadState.status === "error";
  const isEmpty = loadState.status === "ready" && complaints.length === 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <section className="dashboard">
      <h2>Complaints</h2>

      <form className="dashboard-filters" aria-label="Filter complaints">
        <label>
          Category
          <select
            value={filters.category}
            disabled={isLoading}
            onChange={(event) => updateFilter("category", event.target.value as Category | "")}
          >
            <option value="">All</option>
            {CATEGORY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {formatLabel(option)}
              </option>
            ))}
          </select>
        </label>

        <label>
          Priority
          <select
            value={filters.priority}
            disabled={isLoading}
            onChange={(event) => updateFilter("priority", event.target.value as Priority | "")}
          >
            <option value="">All</option>
            {PRIORITY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {formatLabel(option)}
              </option>
            ))}
          </select>
        </label>

        <label>
          Status
          <select
            value={filters.status}
            disabled={isLoading}
            onChange={(event) => updateFilter("status", event.target.value as Status | "")}
          >
            <option value="">All</option>
            {STATUS_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {formatLabel(option)}
              </option>
            ))}
          </select>
        </label>
      </form>

      {isError && (
        <p className="dashboard-error" role="alert">
          Could not load complaints: {loadState.message}
        </p>
      )}

      {isLoading && (
        <p className="dashboard-loading" role="status">
          Loading complaints…
        </p>
      )}

      {!isLoading && !isError && isEmpty && (
        <p className="dashboard-empty">No complaints found.</p>
      )}

      {!isLoading && !isError && complaints.length > 0 && (
        <ul className="complaint-list">
          {complaints.map((complaint) => (
            <li key={complaint.id} className="complaint-card">
              <h3>{complaint.location}</h3>
              <dl className="complaint-meta">
                <dt>Category</dt>
                <dd>{formatLabel(complaint.category)}</dd>
                <dt>Priority</dt>
                <dd>{formatLabel(complaint.priority)}</dd>
                <dt>Status</dt>
                <dd>{formatLabel(complaint.status)}</dd>
              </dl>
              <p className="complaint-text">{snippet(complaint.text)}</p>

              {complaint.allowed_transitions.length > 0 && (
                <div className="complaint-actions">
                  <span className="complaint-actions-label">Change status:</span>
                  {complaint.allowed_transitions.map((nextStatus) => (
                    <button
                      key={nextStatus}
                      type="button"
                      disabled={pendingIds.has(complaint.id)}
                      onClick={() => handleTransition(complaint, nextStatus)}
                    >
                      Mark as {formatLabel(nextStatus)}
                    </button>
                  ))}
                </div>
              )}

              {transitionErrors[complaint.id] && (
                <p className="complaint-error" role="alert">
                  {transitionErrors[complaint.id]}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="dashboard-pagination">
        <button
          type="button"
          onClick={() => setPage((current) => current - 1)}
          disabled={isLoading || page <= 1}
        >
          Prev
        </button>
        <span>
          Page {page} of {totalPages}
        </span>
        <button
          type="button"
          onClick={() => setPage((current) => current + 1)}
          disabled={isLoading || page >= totalPages}
        >
          Next
        </button>
      </div>
    </section>
  );
}

export default DashboardPage;
