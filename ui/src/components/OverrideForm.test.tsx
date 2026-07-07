import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { idis } from "@/lib/idis";

import OverrideForm from "./OverrideForm";

describe("OverrideForm", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("creates an override with type + justification and shows a safe success state", async () => {
    const createSpy = vi.spyOn(idis.overrides, "create").mockResolvedValue({
      override_id: "ovr-1",
      deal_id: "deal-1",
      override_type: "IC_MEMO_CAVEAT",
      justification: "Reviewed manually",
      status: "ACTIVE",
      created_at: "2026-01-01T00:00:00Z",
    });

    render(<OverrideForm dealId="deal-1" />);
    fireEvent.change(screen.getByLabelText(/justification/i), {
      target: { value: "Reviewed manually" },
    });
    fireEvent.change(screen.getByLabelText(/override type/i), {
      target: { value: "IC_MEMO_CAVEAT" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create override/i }));

    await waitFor(() => expect(screen.getByText("ovr-1")).toBeTruthy());
    expect(screen.getByText("ACTIVE")).toBeTruthy();
    expect(createSpy).toHaveBeenCalledWith("deal-1", {
      override_type: "IC_MEMO_CAVEAT",
      justification: "Reviewed manually",
    });
  });

  it("does not submit without a justification", () => {
    const createSpy = vi.spyOn(idis.overrides, "create");
    render(<OverrideForm dealId="deal-1" />);
    fireEvent.click(screen.getByRole("button", { name: /create override/i }));
    expect(createSpy).not.toHaveBeenCalled();
  });

  it("shows an error when create fails", async () => {
    vi.spyOn(idis.overrides, "create").mockRejectedValue(new Error("override boom"));
    render(<OverrideForm dealId="deal-1" />);
    fireEvent.change(screen.getByLabelText(/justification/i), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: /create override/i }));
    await waitFor(() => expect(screen.getByText(/override boom|failed/i)).toBeTruthy());
  });
});
