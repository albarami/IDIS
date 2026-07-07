import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { StrictReadinessReview } from "@/lib/idis";

import StrictReadinessView from "./StrictReadinessView";

const REVIEW: StrictReadinessReview = {
  required: true,
  may_proceed: false,
  blocker_count: 1,
  blocking_components: ["anthropic_extraction"],
  components: [
    {
      component_name: "anthropic_extraction",
      status: "missing-credentials",
      may_proceed: false,
      required_env_vars: ["ANTHROPIC_API_KEY"],
      required_services: ["Anthropic API"],
    },
    {
      component_name: "postgres_rls",
      status: "live-wired-and-used",
      may_proceed: true,
      required_env_vars: [],
      required_services: ["Postgres"],
    },
  ],
};

describe("StrictReadinessView", () => {
  it("shows overall not-ready status and the blocker count", () => {
    render(<StrictReadinessView review={REVIEW} />);
    expect(screen.getByText(/not ready/i)).toBeTruthy();
    expect(screen.getByText(/1 blocker/i)).toBeTruthy();
  });

  it("renders each component's safe-shape: mode, env-var names, service labels", () => {
    render(<StrictReadinessView review={REVIEW} />);
    // Component modes (status enums).
    expect(screen.getByText("missing-credentials")).toBeTruthy();
    expect(screen.getByText("live-wired-and-used")).toBeTruthy();
    // Required env-var NAMES (not values) + service labels.
    expect(screen.getByText("ANTHROPIC_API_KEY")).toBeTruthy();
    expect(screen.getByText("Anthropic API")).toBeTruthy();
    expect(screen.getByText("Postgres")).toBeTruthy();
    // A non-blocking component still appears (table-only, not in blocking list).
    expect(screen.getByText("postgres_rls")).toBeTruthy();
  });

  it("renders a ready state with no blockers", () => {
    render(
      <StrictReadinessView
        review={{
          required: true,
          may_proceed: true,
          blocker_count: 0,
          blocking_components: [],
          components: [],
        }}
      />,
    );
    expect(screen.getByText(/ready/i)).toBeTruthy();
    expect(screen.getByText(/0 blocker/i)).toBeTruthy();
  });

  it("renders required env vars as names only, stripping any =value token", () => {
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
              status: "code-exists-but-not-wired",
              may_proceed: false,
              required_env_vars: ["IDIS_EXTRACT_BACKEND=anthropic"],
              required_services: ["Anthropic API"],
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("IDIS_EXTRACT_BACKEND")).toBeTruthy();
    expect(screen.queryByText("IDIS_EXTRACT_BACKEND=anthropic")).toBeNull();
    // The required VALUE (lowercase "anthropic") must never render.
    expect(screen.queryByText(/anthropic/)).toBeNull();
  });
});
