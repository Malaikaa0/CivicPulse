import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

describe("App", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the app name", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(new Promise(() => {})), // never resolves; not what this test checks
    );

    render(<App />);

    expect(screen.getByRole("heading", { name: "CivicPulse" })).toBeInTheDocument();
  });

  it("shows a checking state before the backend responds", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    render(<App />);

    expect(screen.getByText(/checking backend/i)).toBeInTheDocument();
  });

  it("shows the total once the backend responds", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ total: 32, by_category: {}, by_priority: {}, by_status: {} }),
          { status: 200 },
        ),
      ),
    );

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/32 complaints on file/i)).toBeInTheDocument();
    });
  });

  it("shows an error state when the backend call fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 500 })));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/backend unreachable/i)).toBeInTheDocument();
    });
  });

  it("calls /api/stats, never an absolute backend URL", () => {
    const fetchMock = vi.fn().mockReturnValue(new Promise(() => {}));
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(fetchMock).toHaveBeenCalledWith("/api/stats");
  });
});
