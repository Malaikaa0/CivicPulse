import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Catches errors thrown while rendering any view, so one broken component shows a recovery
 * message instead of unmounting the whole app into a blank page. API failures are handled inside
 * each page; this is the last line of defence for everything else (a bad render, an unexpected
 * shape in a response that a page didn't guard against).
 */
class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Unhandled UI error", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <main className="app" role="alert">
          <h1>Something went wrong</h1>
          <p>This page hit an unexpected error. Your data is safe; reloading usually fixes it.</p>
          <button type="button" onClick={() => window.location.reload()}>
            Reload
          </button>
        </main>
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
