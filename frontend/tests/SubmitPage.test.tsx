import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SubmitPage from "../src/pages/SubmitPage";
import type { Complaint } from "../src/api/client";

function makeComplaint(overrides: Partial<Complaint> = {}): Complaint {
  return {
    id: "9d1f2b1a-0000-4000-8000-000000000001",
    text: "There is a broken water pipe flooding the street.",
    location: "Block 4, Main Street",
    reporter_contact: null,
    category: "water",
    priority: "high",
    status: "open",
    ai_summary: "Water main leak flooding a residential street.",
    triaged_by: "llm:gemini",
    triage_latency_ms: 842,
    created_at: "2026-09-27T10:00:00Z",
    updated_at: "2026-09-27T10:00:00Z",
    allowed_transitions: ["in_progress", "rejected"],
    ...overrides,
  };
}

function fillValidForm() {
  fireEvent.change(screen.getByLabelText(/description/i), {
    target: { value: "There is a broken water pipe flooding the street." },
  });
  fireEvent.change(screen.getByLabelText(/location/i), {
    target: { value: "Block 4, Main Street" },
  });
}

describe("SubmitPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the form", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    render(<SubmitPage />);

    expect(screen.getByLabelText(/description/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/location/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/contact/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /submit/i })).toBeInTheDocument();
  });

  it("shows a validation error for too-short text and disables submit", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    render(<SubmitPage />);

    const textField = screen.getByLabelText(/description/i);
    fireEvent.change(textField, { target: { value: "too short" } });
    fireEvent.blur(textField);

    expect(screen.getByText(/must be at least 10 characters/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /submit/i })).toBeDisabled();
  });

  it("shows an honest submitting state while the request is in flight", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

    render(<SubmitPage />);
    fillValidForm();

    const button = screen.getByRole("button", { name: /submit/i });
    expect(button).not.toBeDisabled();

    fireEvent.click(button);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /submitting/i })).toBeDisabled();
    });
    expect(screen.getByLabelText(/description/i)).toBeDisabled();
  });

  it("renders category, priority, ai_summary, and the triage provider on success", async () => {
    const complaint = makeComplaint();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify(complaint), { status: 201 })),
    );

    render(<SubmitPage />);
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() => {
      expect(screen.getByText(/complaint submitted/i)).toBeInTheDocument();
    });
    expect(screen.getByText("water")).toBeInTheDocument();
    expect(screen.getByText("high")).toBeInTheDocument();
    expect(
      screen.getByText("Water main leak flooding a residential street."),
    ).toBeInTheDocument();
    expect(screen.getByText(/triaged by:\s*llm:gemini/i)).toBeInTheDocument();
  });

  it("handles a null ai_summary gracefully instead of a blank field", async () => {
    const complaint = makeComplaint({ ai_summary: null, triaged_by: "rules:fallback" });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify(complaint), { status: 201 })),
    );

    render(<SubmitPage />);
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() => {
      expect(screen.getByText(/no summary available/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/triaged by:\s*rules:fallback/i)).toBeInTheDocument();
  });

  it("maps a 400 field error onto the matching form field", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: [{ field: "location", message: "Must be at least 3 characters." }],
          }),
          { status: 400 },
        ),
      ),
    );

    render(<SubmitPage />);
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() => {
      expect(screen.getByText("Must be at least 3 characters.")).toBeInTheDocument();
    });
    const locationField = screen.getByLabelText(/location/i);
    expect(locationField).toHaveAttribute("aria-invalid", "true");
  });

  it("shows a clear message with retry time on a 429 rate-limit response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Rate limit exceeded: try later." }), {
          status: 429,
          headers: { "Retry-After": "30" },
        }),
      ),
    );

    render(<SubmitPage />);
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() => {
      expect(screen.getByText(/rate limit exceeded: try later\./i)).toBeInTheDocument();
    });
    expect(screen.getByText(/try again in 30 seconds/i)).toBeInTheDocument();
  });

  it("shows a generic error state on an unexpected failure instead of crashing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));

    render(<SubmitPage />);
    fillValidForm();
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));

    await waitFor(() => {
      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
    });
  });

  it("marks description and location as required, and contact as optional", () => {
    render(<SubmitPage />);

    expect(screen.getByLabelText(/description/i)).toHaveAttribute("aria-required", "true");
    expect(screen.getByLabelText(/location/i)).toHaveAttribute("aria-required", "true");
    expect(screen.getByLabelText(/contact/i)).not.toHaveAttribute("aria-required");
  });
});
