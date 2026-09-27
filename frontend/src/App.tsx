import { useEffect, useState } from "react";
import { getStats, type Stats } from "./api/client";
import SubmitPage from "./pages/SubmitPage";
import DashboardPage from "./pages/DashboardPage";
import StatsPage from "./pages/StatsPage";

type ConnectionState =
  | { status: "checking" }
  | { status: "ok"; stats: Stats }
  | { status: "error"; message: string };

function useBackendConnection(): ConnectionState {
  const [state, setState] = useState<ConnectionState>({ status: "checking" });

  useEffect(() => {
    let cancelled = false;
    getStats()
      .then(({ stats }) => {
        if (!cancelled) setState({ status: "ok", stats });
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
  }, []);

  return state;
}

function ConnectionBadge({ state }: { state: ConnectionState }) {
  if (state.status === "checking") {
    return (
      <span className="status" data-state="checking">
        checking backend…
      </span>
    );
  }
  if (state.status === "error") {
    return (
      <span className="status" data-state="error">
        backend unreachable: {state.message}
      </span>
    );
  }
  return (
    <span className="status" data-state="ok">
      connected · {state.stats.total} complaints on file
    </span>
  );
}

type Tab = "home" | "submit" | "dashboard" | "stats";

const TABS: { id: Tab; label: string }[] = [
  { id: "home", label: "Home" },
  { id: "submit", label: "Submit a complaint" },
  { id: "dashboard", label: "Dashboard" },
  { id: "stats", label: "Stats" },
];

function App() {
  const connection = useBackendConnection();
  const [tab, setTab] = useState<Tab>("home");

  return (
    <>
      <nav className="app-nav" aria-label="Main">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            className="app-nav-tab"
            aria-current={tab === id ? "page" : undefined}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === "home" && (
        <main className="app">
          <h1>CivicPulse</h1>
          <p>Municipal complaint intake and triage.</p>
          <ConnectionBadge state={connection} />
        </main>
      )}
      {tab === "submit" && <SubmitPage />}
      {tab === "dashboard" && <DashboardPage />}
      {tab === "stats" && <StatsPage />}
    </>
  );
}

export default App;
