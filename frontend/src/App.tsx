import { useEffect, useState } from "react";
import { getStats, type Stats } from "./api/client";

type ConnectionState =
  | { status: "checking" }
  | { status: "ok"; stats: Stats }
  | { status: "error"; message: string };

function useBackendConnection(): ConnectionState {
  const [state, setState] = useState<ConnectionState>({ status: "checking" });

  useEffect(() => {
    let cancelled = false;
    getStats()
      .then((stats) => {
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

function App() {
  const connection = useBackendConnection();

  return (
    <main className="app">
      <h1>CivicPulse</h1>
      <p>Municipal complaint intake and triage.</p>
      <ConnectionBadge state={connection} />
    </main>
  );
}

export default App;
