import { useCallback, useEffect, useState } from "react";
import { getStats, type Stats } from "../api/client";

type StatsState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ok"; stats: Stats; cacheStatus: string | null };

function useStats() {
  const [state, setState] = useState<StatsState>({ status: "loading" });
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    getStats()
      .then(({ stats, cacheStatus }) => {
        if (!cancelled) setState({ status: "ok", stats, cacheStatus });
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({
            status: "error",
            message: error instanceof Error ? error.message : "unknown error",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  const refresh = useCallback(() => setReloadToken((token) => token + 1), []);

  return { state, refresh };
}

function CacheBadge({ cacheStatus }: { cacheStatus: string | null }) {
  if (cacheStatus === "HIT") {
    return (
      <span className="stats-cache" data-state="hit">
        Cache: HIT
      </span>
    );
  }
  if (cacheStatus === "MISS") {
    return (
      <span className="stats-cache" data-state="miss">
        Cache: MISS
      </span>
    );
  }
  return (
    <span className="stats-cache" data-state="unknown">
      Cache: unknown
    </span>
  );
}

function StatsGroup({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = Object.entries(data);

  return (
    <section className="stats-group">
      <h2>{title}</h2>
      {entries.length === 0 ? (
        <p className="stats-empty">No data yet.</p>
      ) : (
        <ul className="stats-list">
          {entries.map(([key, count]) => (
            <li key={key} className="stats-list-item">
              <span className="stats-list-label">{key}</span>
              <span className="stats-list-count">{count}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function StatsPage() {
  const { state, refresh } = useStats();

  return (
    <main className="app stats-page">
      <div className="stats-header">
        <h1>Complaint stats</h1>
        <div className="stats-toolbar">
          {state.status === "ok" && <CacheBadge cacheStatus={state.cacheStatus} />}
          <button
            type="button"
            className="stats-refresh"
            onClick={refresh}
            disabled={state.status === "loading"}
          >
            Refresh
          </button>
        </div>
      </div>

      {state.status === "loading" && (
        <p className="status" data-state="checking">
          Loading stats…
        </p>
      )}

      {state.status === "error" && (
        <p className="status" data-state="error">
          Failed to load stats: {state.message}
        </p>
      )}

      {state.status === "ok" && (
        <>
          <p className="stats-total">
            Total complaints: <strong>{state.stats.total}</strong>
          </p>
          <StatsGroup title="By category" data={state.stats.by_category} />
          <StatsGroup title="By priority" data={state.stats.by_priority} />
          <StatsGroup title="By status" data={state.stats.by_status} />
        </>
      )}
    </main>
  );
}

export default StatsPage;
