import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunStep } from "@/lib/idis";

import RunStepLedger from "./RunStepLedger";

const STEPS: RunStep[] = [
  { step_name: "EXTRACT", status: "COMPLETED", retry_count: 0 },
  {
    step_name: "DEBATE",
    status: "FAILED",
    error: { code: "DEBATE_TIMEOUT", message: "Debate timed out" },
    retry_count: 2,
  },
];

describe("RunStepLedger", () => {
  it("renders each step's name, status, retries, and safe error code", () => {
    render(<RunStepLedger steps={STEPS} blockReason={null} />);
    expect(screen.getByText("EXTRACT")).toBeTruthy();
    expect(screen.getByText("DEBATE")).toBeTruthy();
    expect(screen.getByText("COMPLETED")).toBeTruthy();
    expect(screen.getByText("FAILED")).toBeTruthy();
    // Safe error CODE surfaces; the free-text message is not rendered.
    expect(screen.getByText("DEBATE_TIMEOUT")).toBeTruthy();
    expect(screen.queryByText(/Debate timed out/)).toBeNull();
  });

  it("shows the blocker reason (blocker code) when present", () => {
    render(<RunStepLedger steps={[]} blockReason="STRICT_FULL_LIVE_BLOCKED" />);
    expect(screen.getByText(/STRICT_FULL_LIVE_BLOCKED/)).toBeTruthy();
  });
});
