import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import StatsPage from "../src/pages/StatsPage";

const realisticStats = {
  total: 7,
  by_category: { water: 5, electricity: 2 },
  by_priority: { high: 1, low: 6 },
  by_status: { open: 4, closed: 3 },
};

function jsonResponse(body: unknown, headers: Record<string, string> = {}, status = 200) {
  return new Response(JSON.stringify(body), { status, headers });
}

describe("StatsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the total and per-category/priority/status breakdowns", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(realisticStats, { "X-Cache": "HIT" })),
    );

    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText("7", { exact: false })).toBeInTheDocument();
    });

    expect(screen.getByText("water")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("electricity")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(screen.getByText("low")).toBeInTheDocument();
    expect(screen.getByText("open")).toBeInTheDocument();
    expect(screen.getByText("closed")).toBeInTheDocument();
  });

  it('shows "Cache: MISS" when the X-Cache header is MISS', async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(realisticStats, { "X-Cache": "MISS" })),
    );

    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText("Cache: MISS")).toBeInTheDocument();
    });
  });

  it('shows "Cache: HIT" when the X-Cache header is HIT', async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(realisticStats, { "X-Cache": "HIT" })),
    );

    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText("Cache: HIT")).toBeInTheDocument();
    });
  });

  it("shows Cache: unknown when the X-Cache header is absent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(realisticStats)));

    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText("Cache: unknown")).toBeInTheDocument();
    });
  });

  it("shows a loading state before the response resolves", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(new Promise(() => {})), // never resolves
    );

    render(<StatsPage />);

    expect(screen.getByText(/loading stats/i)).toBeInTheDocument();
  });

  it("shows an error state when the fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 500 })));

    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText(/failed to load stats/i)).toBeInTheDocument();
    });
  });

  it("renders an empty group without crashing when a breakdown is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ total: 0, by_category: {}, by_priority: {}, by_status: {} }),
      ),
    );

    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getAllByText(/no data yet/i)).toHaveLength(3);
    });
  });

  it("re-fetches and can transition from MISS to HIT when Refresh is clicked", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(realisticStats, { "X-Cache": "MISS" }))
      .mockResolvedValueOnce(jsonResponse(realisticStats, { "X-Cache": "HIT" }));
    vi.stubGlobal("fetch", fetchMock);

    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText("Cache: MISS")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => {
      expect(screen.getByText("Cache: HIT")).toBeInTheDocument();
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
