import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { idis, type HumanGate, type HumanGateActionResult } from "@/lib/idis";

import HumanGateActions from "./HumanGateActions";

const GATE: HumanGate = {
  gate_id: "gate-1",
  deal_id: "deal-1",
  gate_type: "IC_APPROVAL",
  status: "PENDING",
  created_at: "2026-01-01T00:00:00Z",
};

const ACTION: HumanGateActionResult = {
  action_id: "act-1",
  gate_id: "gate-1",
  action: "APPROVE",
  actor_id: "actor-1",
  created_at: "2026-01-01T00:00:00Z",
};

describe("HumanGateActions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("submits APPROVE for the gate, confirms safely, and notifies the parent", async () => {
    const submitSpy = vi.spyOn(idis.humanGates, "submit").mockResolvedValue(ACTION);
    const onActionComplete = vi.fn();
    render(<HumanGateActions dealId="deal-1" gate={GATE} onActionComplete={onActionComplete} />);

    fireEvent.click(screen.getByRole("button", { name: /approve/i }));

    await waitFor(() =>
      expect(submitSpy).toHaveBeenCalledWith("deal-1", { gate_id: "gate-1", action: "APPROVE" }),
    );
    expect(onActionComplete).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/submitted/i)).toBeTruthy();
  });

  it("supports REJECT and CORRECT actions with the safe action enum", async () => {
    const submitSpy = vi.spyOn(idis.humanGates, "submit").mockResolvedValue(ACTION);
    render(<HumanGateActions dealId="deal-1" gate={GATE} onActionComplete={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /reject/i }));
    await waitFor(() =>
      expect(submitSpy).toHaveBeenCalledWith("deal-1", { gate_id: "gate-1", action: "REJECT" }),
    );

    fireEvent.click(screen.getByRole("button", { name: /correct/i }));
    await waitFor(() =>
      expect(submitSpy).toHaveBeenCalledWith("deal-1", { gate_id: "gate-1", action: "CORRECT" }),
    );
  });

  it("shows an error when the action fails", async () => {
    vi.spyOn(idis.humanGates, "submit").mockRejectedValue(new Error("gate boom"));
    render(<HumanGateActions dealId="deal-1" gate={GATE} onActionComplete={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /approve/i }));
    await waitFor(() => expect(screen.getByText(/gate boom|failed/i)).toBeTruthy());
  });
});
