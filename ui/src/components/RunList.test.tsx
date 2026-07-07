import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { RunListItem } from "@/lib/idis";

import RunList from "./RunList";

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

const RUNS: RunListItem[] = [
  {
    run_id: "run-1",
    deal_id: "deal-1",
    status: "COMPLETED",
    mode: "FULL",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T01:00:00Z",
  },
  {
    run_id: "run-2",
    deal_id: "deal-1",
    status: "RUNNING",
    mode: "SNAPSHOT",
    started_at: "2026-01-02T00:00:00Z",
    finished_at: null,
  },
];

describe("RunList", () => {
  it("renders each run's safe summary and links to the run detail", () => {
    render(<RunList runs={RUNS} />);
    expect(screen.getByText("COMPLETED")).toBeTruthy();
    expect(screen.getByText("RUNNING")).toBeTruthy();
    expect(screen.getByText("FULL")).toBeTruthy();
    expect(screen.getByText("SNAPSHOT")).toBeTruthy();

    const link = screen.getByRole("link", { name: "run-1" });
    expect(link.getAttribute("href")).toBe("/runs/run-1");
  });

  it("renders an empty state when there are no runs", () => {
    render(<RunList runs={[]} />);
    expect(screen.getByText(/no runs/i)).toBeTruthy();
  });
});
