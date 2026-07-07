import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import RunStepLedger from "@/components/RunStepLedger";
import StrictReadinessView from "@/components/StrictReadinessView";

import { idis, type Run, type RunStatus } from "./idis";

describe("Slice95 UI<->backend contract — client surface", () => {
  it("exposes exactly the review method groups the pages consume", () => {
    expect(typeof idis.readiness.get).toBe("function");
    expect(typeof idis.runs.list).toBe("function");
    expect(typeof idis.runs.get).toBe("function");
    expect(typeof idis.documents.upload).toBe("function");
    expect(typeof idis.humanGates.list).toBe("function");
    expect(typeof idis.humanGates.submit).toBe("function");
    expect(typeof idis.overrides.create).toBe("function");
  });
});

describe("Slice95 UI<->backend contract — safe-shape rendering boundaries", () => {
  it("RunStepLedger surfaces the error CODE but never the free-text error message", () => {
    render(
      <RunStepLedger
        steps={[
          {
            step_name: "DEBATE",
            status: "FAILED",
            error: { code: "E_CODE", message: "raw private detail" },
          },
        ]}
        blockReason={null}
      />,
    );
    expect(screen.getByText("E_CODE")).toBeTruthy();
    expect(screen.queryByText(/raw private detail/)).toBeNull();
  });

  it("StrictReadinessView renders required env-var NAMES only, never a =value", () => {
    render(
      <StrictReadinessView
        review={{
          required: true,
          may_proceed: false,
          blocker_count: 1,
          blocking_components: ["extraction"],
          components: [
            {
              component_name: "extraction",
              status: "missing-credentials",
              may_proceed: false,
              required_env_vars: ["MY_VAR=secretvalue"],
              required_services: [],
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("MY_VAR")).toBeTruthy();
    expect(screen.queryByText(/secretvalue/)).toBeNull();
  });
});

describe("Slice95 UI<->backend contract — run status enum", () => {
  it("RunStatus accepts every backend/static run status, including CANCELLED", () => {
    // Compile-time contract: the static OpenAPI RunStatus/RunRef enum includes CANCELLED, so the
    // consumed UI type must too (StatusBadge keys off RunStatus; the run detail reads Run.status).
    const statuses: RunStatus[] = ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"];
    expect(statuses).toContain("CANCELLED");
    const run: Run = { run_id: "r1", status: "CANCELLED", started_at: "2026-01-01T00:00:00Z" };
    expect(run.status).toBe("CANCELLED");
  });
});
