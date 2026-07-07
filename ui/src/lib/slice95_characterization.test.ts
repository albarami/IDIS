// Slice95 Task 1 — characterization: pin the as-built UI client surface + the client-side gaps.
//
// GREEN-on-arrival. Pins the review client groups a reviewer already has, and the client gaps
// that later tasks flip: no readiness client (Task 5 / DEC-D), no data-room documents/upload
// client (Task 6), no runs.list (Task 8 / run-list). Any RED here is a real as-built surprise.

import { describe, expect, it } from "vitest";

import { idis } from "./idis";

describe("slice95 as-built UI client surface", () => {
  it("exposes the existing review client groups", () => {
    const keys = Object.keys(idis);
    for (const group of [
      "deals",
      "claims",
      "deliverables",
      "runs",
      "debate",
      "humanGates",
      "overrides",
      "audit",
    ]) {
      expect(keys).toContain(group);
    }
  });

  it("existing groups carry their current review methods", () => {
    expect(typeof idis.deals.getTruthDashboard).toBe("function");
    expect(typeof idis.claims.getSanad).toBe("function");
    expect(typeof idis.deliverables.getManifest).toBe("function");
    expect(typeof idis.runs.get).toBe("function");
    expect(typeof idis.humanGates.submit).toBe("function");
    expect(typeof idis.overrides.create).toBe("function");
    expect(typeof idis.debate.get).toBe("function");
  });
});

describe("slice95 UI client surface (previously-gap groups, now shipped)", () => {
  it("exposes the readiness client group (Task 5)", () => {
    expect(typeof idis.readiness.get).toBe("function");
  });

  it("exposes the data-room documents/upload client group (Task 6)", () => {
    expect(typeof idis.documents.upload).toBe("function");
  });

  it("exposes runs.list (Task 8)", () => {
    expect(typeof idis.runs.list).toBe("function");
  });
});
