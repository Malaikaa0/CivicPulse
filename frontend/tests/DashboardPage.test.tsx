import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DashboardPage from "../src/pages/DashboardPage";
import type { Complaint, ComplaintPage } from "../src/api/client";

function makeComplaint(overrides: Partial<Complaint> = {}): Complaint {
  return {
    id: "c-1",
    text: "Water main burst on Main Street, flooding the intersection.",
    location: "Main Street",
    reporter_contact: null,
    category: "water",
    priority: "high",
    status: "open",
    ai_summary: null,
    triaged_by: "simulated",
    triage_latency_ms: 12,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    allowed_transitions: ["in_progress", "rejected"],
    ...overrides,
  };
}

function pageResponse(items: Complaint[], overrides: Partial<ComplaintPage> = {}): Response {
  const body: ComplaintPage = {
    items,
    total: items.length,
    page: 1,
    page_size: 10,
    ...overrides,
  };
  return new Response(JSON.stringify(body), { status: 200 });
}

describe("DashboardPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders a list of complaints", async () => {
    const complaints = [
      makeComplaint({ id: "c-1", location: "Main Street", category: "water" }),
      makeComplaint({
        id: "c-2",
        location: "Oak Avenue",
        category: "roads",
        priority: "low",
        status: "in_progress",
        text: "Pothole near the school crossing.",
        allowed_transitions: ["resolved"],
      }),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(pageResponse(complaints, { total: 2 })),
    );

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText("Main Street")).toBeInTheDocument();
    });
    expect(screen.getByText("Oak Avenue")).toBeInTheDocument();
    expect(screen.getByText(/pothole near the school crossing/i)).toBeInTheDocument();
    expect(screen.getAllByText("water", { exact: false }).length).toBeGreaterThan(0);
  });

  it("shows an empty state (not a loading state) when there are no results", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(pageResponse([], { total: 0 })));

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText(/no complaints found/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/loading complaints/i)).not.toBeInTheDocument();
  });

  it("shows a visible error state when the fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 500 })));

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/could not load complaints/i);
    });
    expect(screen.queryByText(/loading complaints/i)).not.toBeInTheDocument();
  });

  it("Next/Prev pagination controls call fetch with the right page", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(pageResponse([makeComplaint()], { total: 25, page_size: 10 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => {
      expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
    });
    const nextCallUrl = fetchMock.mock.calls[1][0] as string;
    expect(nextCallUrl).toContain("page=2");

    fireEvent.click(screen.getByRole("button", { name: "Prev" }));

    await waitFor(() => {
      expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    });
    const prevCallUrl = fetchMock.mock.calls[2][0] as string;
    expect(prevCallUrl).toContain("page=1");
  });

  it("changing a filter re-fetches with the right query params and resets to page 1", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(pageResponse([makeComplaint()], { total: 25, page_size: 10 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => {
      expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "roads" } });

    await waitFor(() => {
      expect(screen.getByText("Page 1 of 3")).toBeInTheDocument();
    });
    const lastCallUrl = fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0] as string;
    expect(lastCallUrl).toContain("category=roads");
    expect(lastCallUrl).toContain("page=1");
  });

  it("only offers allowed_transitions as status change options for a complaint", async () => {
    const complaint = makeComplaint({ allowed_transitions: ["in_progress"] });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(pageResponse([complaint], { total: 1 })));

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText("Main Street")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: /mark as in progress/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /mark as resolved/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /mark as rejected/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /mark as open/i })).not.toBeInTheDocument();
  });

  it("a successful status change updates the displayed status without a full refetch", async () => {
    const complaint = makeComplaint({ allowed_transitions: ["in_progress", "rejected"] });
    const updated = makeComplaint({ status: "in_progress", allowed_transitions: ["resolved"] });

    const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return Promise.resolve(new Response(JSON.stringify(updated), { status: 200 }));
      }
      return Promise.resolve(pageResponse([complaint], { total: 1 }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /mark as in progress/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /mark as in progress/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /mark as resolved/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: /mark as rejected/i })).not.toBeInTheDocument();
    expect(screen.getByRole("listitem")).toHaveTextContent("in progress");
  });

  it("shows the server's exact 409 conflict message", async () => {
    const complaint = makeComplaint({ allowed_transitions: ["in_progress"] });
    const conflictMessage = "Complaint already resolved; cannot move to in_progress.";

    const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        return Promise.resolve(
          new Response(
            JSON.stringify({ detail: conflictMessage, current: "resolved", requested: "in_progress" }),
            { status: 409 },
          ),
        );
      }
      return Promise.resolve(pageResponse([complaint], { total: 1 }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /mark as in progress/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /mark as in progress/i }));

    await waitFor(() => {
      const cardAlert = within(screen.getByText("Main Street").closest("li")!).getByRole("alert");
      expect(cardAlert).toHaveTextContent(conflictMessage);
    });
  });

  it("ignores a status change that finishes after the dashboard has unmounted", async () => {
    let resolveChange: (value: Response) => void = () => {};
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(pageResponse([makeComplaint()]))
      .mockReturnValueOnce(
        new Promise<Response>((resolve) => {
          resolveChange = resolve;
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    const { unmount } = render(<DashboardPage />);
    fireEvent.click(await screen.findByRole("button", { name: /mark as in progress/i }));
    unmount();

    // The request resolves after the user has already navigated away.
    resolveChange(
      new Response(JSON.stringify(makeComplaint({ status: "in_progress", allowed_transitions: [] })), {
        status: 200,
      }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(consoleError).not.toHaveBeenCalled();
  });
});
