import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { startTransition } from "react";
import { createRequire } from "node:module";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

const nav = vi.hoisted(() => {
  const router = { push: vi.fn() };
  return { router, dealId: "deal-1" };
});

vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  useParams: () => ({ dealId: nav.dealId }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

import TruthDashboardPage from "./page";

const CDD_A = "9f8e7d6c-5b4a-43c2-9182-736455667788";
const CDD_B = "8a9b0c1d-2e3f-4a5b-8c9d-0e1f2a3b4c5d";
const FDD_A = "1a2b3c4d-5e6f-47a8-9b0c-1d2e3f4a5b6c";
const FDD_B = "f0e1d2c3-b4a5-4968-8796-a5b4c3d2e1f0";
const IMPORTED_CDD = "aaaaaaaa-0000-4000-8000-000000000001";

const DEAL_NAME_PREFIX = "Deal ";

const DASHBOARD = {
  deal_id: "deal-1",
  summary: {
    total_claims: 1,
    by_grade: { A: 1, B: 0, C: 0, D: 0 },
    by_verdict: { VERIFIED: 1, INFLATED: 0, CONTRADICTED: 0, UNVERIFIED: 0, SUBJECTIVE: 0 },
    fatal_defects: 0,
  },
  claims: { items: [], next_cursor: null },
};

function methodologyRecord(id: string, type: string, label: string, status: string) {
  return {
    methodology_version_record_id: id,
    methodology_version_id: "mv-" + label.toLowerCase().replace(/[^a-z0-9]+/g, "-"),
    methodology_type: type,
    version_label: label,
    source_sha256: "0".repeat(64),
    source_byte_count: 1024,
    source_contract_hash: "1".repeat(64),
    source_trace_digest: "2".repeat(64),
    registry_hash: "3".repeat(64),
    version_hash: "4".repeat(64),
    question_count: 10,
    source_section_count: 3,
    header_row: null,
    status,
    imported_at: "2026-01-01T00:00:00Z",
    frozen_at: status === "FROZEN" ? "2026-01-02T00:00:00Z" : null,
  };
}

const CATALOG = [
  methodologyRecord(CDD_A, "commercial_dd", "CDD v2.1", "FROZEN"),
  methodologyRecord(CDD_B, "commercial_dd", "CDD v1.0", "FROZEN"),
  methodologyRecord(IMPORTED_CDD, "commercial_dd", "CDD v3.0 draft", "IMPORTED"),
  methodologyRecord(FDD_A, "financial_dd", "FDD v4.0", "FROZEN"),
  methodologyRecord(FDD_B, "financial_dd", "FDD v3.5", "FROZEN"),
];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function deferred<T>() {
  let resolve!: (_value: T) => void;
  let reject!: (_reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// Real React commit-boundary helpers. A synchronous commit inside
// flushSync is a SyncLane commit whose passive effects React flushes
// inside the same commit, and a fast concurrent commit lets the scheduler
// run the commit's queued passive effects inside the same work burst, so
// neither leaves the commit-before-passive-cleanup interval reachable.
// Instead the boundary update runs inside a transition so it is a real
// concurrent commit task on the React Scheduler's own queue, followed on
// that queue by a busy task that keeps the burst past its ~5 ms yield
// deadline and then by the settle task for the pending launch. When the
// burst yields, the commit's queued passive effects land in a later
// scheduler task, so the launch's promise continuations run at the burst
// boundary inside the commit-before-passive window — the same interval a
// busy main thread opens in production. This is the same scheduler
// instance react-dom uses; nothing about the page or its effects is
// mocked or copied.
const COMMIT_PHASE_OCCUPY_MS = 10;

interface ReactScheduler {
  unstable_scheduleCallback: (
    _priorityLevel: number,
    _callback: () => null | void,
  ) => unknown;
  unstable_NormalPriority: number;
}

const scheduler: ReactScheduler = createRequire(import.meta.url)("scheduler");

// Queues, in order on the scheduler's own task queue: the boundary
// commit task, the settle task for the pending launch, and a busy task
// that consumes the burst's yield deadline. The commit and the settle run
// inside the first burst; the busy task then pushes the burst past the
// deadline so the commit's queued passive effects are deferred to a later
// burst, and the launch's continuations run at the burst boundary inside
// the commit-before-passive window.
function queueBoundaryCommit(renderUpdate: () => void, settle: () => void) {
  startTransition(renderUpdate);
  scheduler.unstable_scheduleCallback(scheduler.unstable_NormalPriority, () => {
    settle();
    return null;
  });
  scheduler.unstable_scheduleCallback(scheduler.unstable_NormalPriority, () => {
    const end = Date.now() + COMMIT_PHASE_OCCUPY_MS;
    while (Date.now() < end) {
      // keep the scheduler's current work burst busy past its yield deadline
    }
    return null;
  });
}

function renderCommittedPage() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  root.render(<TruthDashboardPage />);
  return { container, root };
}

async function drainMicrotasks(turns = 60): Promise<void> {
  for (let i = 0; i < turns; i++) {
    await Promise.resolve();
  }
}

type FetchImpl = (_input: RequestInfo | URL, _init?: RequestInit) => Promise<Response>;
type FetchCall = Parameters<FetchImpl>;

describe("TruthDashboardPage FULL launch methodology selection", () => {
  let catalogHandler: () => Response | Promise<Response>;
  let runHandler: (_init: RequestInit | undefined) => Response | Promise<Response>;
  let dataGate: Promise<void> | null;
  let fetchMock: Mock<FetchImpl>;

  function runPostCalls(): FetchCall[] {
    return fetchMock.mock.calls.filter(
      (call) => (call[1]?.method ?? "GET") === "POST" && String(call[0]).includes("/runs"),
    );
  }

  beforeEach(() => {
    nav.router.push.mockReset();
    nav.dealId = "deal-1";
    catalogHandler = () => jsonResponse(200, CATALOG);
    runHandler = () => jsonResponse(202, { run_id: "run-fixed-1", status: "QUEUED" });
    dataGate = null;
    fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/v1/methodology-versions")) {
        return catalogHandler();
      }
      if (method === "POST" && /\/v1\/deals\/[^/?]+\/runs$/.test(url)) {
        return runHandler(init);
      }
      const id = url.match(/\/v1\/deals\/([^/?]+)/)?.[1] ?? "deal-1";
      if (dataGate !== null) {
        await dataGate;
      }
      if (url.includes("/truth-dashboard")) {
        return jsonResponse(200, { ...DASHBOARD, deal_id: id });
      }
      if (url.includes("/deliverables")) {
        return jsonResponse(200, { items: [] });
      }
      if (url.includes("/human-gates")) {
        return jsonResponse(200, { items: [] });
      }
      if (/\/v1\/deals\/[^/?]+$/.test(url)) {
        return jsonResponse(200, {
          deal_id: id,
          name: DEAL_NAME_PREFIX + id,
          company_name: "Acme Corp",
          status: "INTAKE",
          stage: "SEED",
          created_at: "2026-01-01T00:00:00Z",
        });
      }
      return jsonResponse(500, {
        code: "unrouted",
        message: "Unexpected fetch " + method + " " + url,
      });
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("requires explicit CDD+FDD choices and submits the exact frozen pair", async () => {
    render(<TruthDashboardPage />);
    await screen.findByText("Total Claims");

    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    const runButton = screen.getByRole("button", { name: "Run" });

    // No automatic first/latest/only/default selection.
    expect(cdd).toHaveProperty("value", "");
    expect(fdd).toHaveProperty("value", "");
    expect(runButton).toHaveProperty("disabled", true);

    fireEvent.change(cdd, { target: { value: CDD_A } });
    expect(runButton).toHaveProperty("disabled", true);

    fireEvent.change(fdd, { target: { value: FDD_B } });
    expect(runButton).toHaveProperty("disabled", false);

    fireEvent.click(runButton);

    await waitFor(() => expect(runPostCalls()).toHaveLength(1));
    const [url, init] = runPostCalls()[0];
    expect(String(url)).toBe("/api/idis/v1/deals/deal-1/runs");
    const headers = new Headers(init?.headers);
    expect(headers.get("Idempotency-Key")).toBeTruthy();
    expect(headers.get("X-Request-Id")).toBeTruthy();
    expect(JSON.parse(String(init?.body))).toEqual({
      mode: "FULL",
      methodology_selection: {
        commercial_dd_version_record_id: CDD_A,
        financial_dd_version_record_id: FDD_B,
      },
    });

    await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith("/runs/run-fixed-1"));
  });

  it("keeps launch unavailable while the methodology catalog is still loading", async () => {
    const pending = deferred<Response>();
    catalogHandler = () => pending.promise;

    render(<TruthDashboardPage />);
    await screen.findByText("Total Claims");

    const runButton = screen.getByRole("button", { name: "Run" });
    expect(runButton).toHaveProperty("disabled", true);
    fireEvent.click(runButton);
    expect(runPostCalls()).toHaveLength(0);

    await act(async () => {
      pending.resolve(jsonResponse(200, CATALOG));
    });

    await screen.findByLabelText(/commercial dd methodology/i);
    // Catalog arrival alone must not launch or preselect.
    expect(runButton).toHaveProperty("disabled", true);
    expect(runPostCalls()).toHaveLength(0);
  });

  it("freezes the submitted pair and blocks duplicate submits while a run is pending", async () => {
    const pendingRun = deferred<Response>();
    runHandler = () => pendingRun.promise;

    render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    const runButton = screen.getByRole("button", { name: "Run" });

    fireEvent.change(cdd, { target: { value: CDD_A } });
    fireEvent.change(fdd, { target: { value: FDD_A } });
    fireEvent.click(runButton);

    await waitFor(() => expect(runPostCalls()).toHaveLength(1));

    // Changing a choice mid-flight must not mutate the in-flight request.
    fireEvent.change(cdd, { target: { value: CDD_B } });
    fireEvent.click(runButton);
    expect(runPostCalls()).toHaveLength(1);

    await act(async () => {
      pendingRun.resolve(jsonResponse(202, { run_id: "run-fixed-1", status: "QUEUED" }));
    });

    const [, init] = runPostCalls()[0];
    expect(JSON.parse(String(init?.body))).toEqual({
      mode: "FULL",
      methodology_selection: {
        commercial_dd_version_record_id: CDD_A,
        financial_dd_version_record_id: FDD_A,
      },
    });
    await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith("/runs/run-fixed-1"));
    expect(nav.router.push).toHaveBeenCalledTimes(1);
  });

  it("does not reinstall stale catalog choices after a deal change", async () => {
    const stale = deferred<Response>();
    catalogHandler = () => stale.promise;

    const view = render(<TruthDashboardPage />);
    await screen.findByText(DEAL_NAME_PREFIX + "deal-1");

    const freshCatalog = [
      methodologyRecord(CDD_B, "commercial_dd", "CDD v1.0", "FROZEN"),
      methodologyRecord(FDD_B, "financial_dd", "FDD v3.5", "FROZEN"),
    ];
    catalogHandler = () => jsonResponse(200, freshCatalog);
    nav.dealId = "deal-2";
    view.rerender(<TruthDashboardPage />);

    await screen.findByText(DEAL_NAME_PREFIX + "deal-2");
    await screen.findByRole("option", { name: /CDD v1\.0/ });

    // The stale deal-1 catalog resolves late and must not be installed.
    await act(async () => {
      stale.resolve(
        jsonResponse(200, [
          methodologyRecord(CDD_A, "commercial_dd", "CDD v2.1", "FROZEN"),
          methodologyRecord(FDD_A, "financial_dd", "FDD v4.0", "FROZEN"),
        ]),
      );
    });

    expect(screen.queryByRole("option", { name: /CDD v2\.1/ })).toBeNull();
    expect(screen.queryByRole("option", { name: /FDD v4\.0/ })).toBeNull();

    const cdd = screen.getByLabelText(/commercial dd methodology/i);
    const fdd = screen.getByLabelText(/financial dd methodology/i);
    expect(cdd).toHaveProperty("value", "");
    expect(fdd).toHaveProperty("value", "");
    expect(screen.getByRole("button", { name: "Run" })).toHaveProperty("disabled", true);
    expect(runPostCalls()).toHaveLength(0);
  });

  it("shows the conflict error with its request id and does not navigate on 409", async () => {
    runHandler = () =>
      jsonResponse(409, {
        code: "METHODOLOGY_COVERAGE_INCOMPLETE",
        message: "Methodology coverage incomplete for selected records",
        request_id: "req-conflict-9",
      });

    render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd, { target: { value: CDD_A } });
    fireEvent.change(fdd, { target: { value: FDD_A } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await screen.findByText("Methodology coverage incomplete for selected records");
    await screen.findByText(/req-conflict-9/);
    expect(nav.router.push).not.toHaveBeenCalledWith("/runs/run-fixed-1");
    // No automatic retry or reselection.
    expect(runPostCalls()).toHaveLength(1);
  });

  it("redirects to login when the run launch returns 401", async () => {
    runHandler = () =>
      jsonResponse(401, { code: "unauthorized", message: "Not authenticated" });

    render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd, { target: { value: CDD_A } });
    fireEvent.change(fdd, { target: { value: FDD_A } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith("/login"));
  });

  it("keeps the loaded dashboard visible when the methodology catalog fails", async () => {
    catalogHandler = () =>
      jsonResponse(500, {
        code: "internal_error",
        message: "Methodology catalog unreachable",
        request_id: "req-catalog-7",
      });

    render(<TruthDashboardPage />);

    // Already-loaded dashboard content is preserved.
    await screen.findByText("Total Claims");
    expect(screen.getByText("Claims (0)")).toBeTruthy();

    await screen.findByText("Methodology catalog unreachable");
    await screen.findByText(/req-catalog-7/);

    expect(screen.getByRole("button", { name: "Run" })).toHaveProperty("disabled", true);
    expect(runPostCalls()).toHaveLength(0);
  });

  it("cannot launch when only one methodology family has a frozen version", async () => {
    catalogHandler = () =>
      jsonResponse(200, [
        methodologyRecord(CDD_A, "commercial_dd", "CDD v2.1", "FROZEN"),
      ]);

    render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);

    fireEvent.change(cdd, { target: { value: CDD_A } });
    expect(fdd).toHaveProperty("value", "");
    await screen.findByText(/no frozen financial dd/i);

    const runButton = screen.getByRole("button", { name: "Run" });
    expect(runButton).toHaveProperty("disabled", true);
    fireEvent.click(runButton);
    expect(runPostCalls()).toHaveLength(0);
  });

  it("offers no eligible choice when catalog records are only IMPORTED", async () => {
    catalogHandler = () =>
      jsonResponse(200, [
        methodologyRecord(IMPORTED_CDD, "commercial_dd", "CDD v3.0 draft", "IMPORTED"),
        methodologyRecord(FDD_A, "financial_dd", "FDD v5.0 draft", "IMPORTED"),
      ]);

    render(<TruthDashboardPage />);
    await screen.findByLabelText(/commercial dd methodology/i);
    await screen.findByText(/no frozen commercial dd/i);
    await screen.findByText(/no frozen financial dd/i);

    expect(screen.getByRole("button", { name: "Run" })).toHaveProperty("disabled", true);
    expect(runPostCalls()).toHaveLength(0);
  });

  it("keeps launch disabled and posts nothing on an empty catalog", async () => {
    catalogHandler = () => jsonResponse(200, []);

    render(<TruthDashboardPage />);
    await screen.findByLabelText(/commercial dd methodology/i);
    await screen.findByText(/no frozen commercial dd/i);
    await screen.findByText(/no frozen financial dd/i);

    const runButton = screen.getByRole("button", { name: "Run" });
    expect(runButton).toHaveProperty("disabled", true);
    fireEvent.click(runButton);
    expect(runPostCalls()).toHaveLength(0);
  });

  it("refuses a pair that reuses the same record id across both families", async () => {
    const sharedId = "11111111-2222-4333-8444-555555555555";
    catalogHandler = () =>
      jsonResponse(200, [
        methodologyRecord(sharedId, "commercial_dd", "CDD v9.9", "FROZEN"),
        methodologyRecord(sharedId, "financial_dd", "FDD v9.9", "FROZEN"),
      ]);

    render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);

    fireEvent.change(cdd, { target: { value: sharedId } });
    fireEvent.change(fdd, { target: { value: sharedId } });

    const runButton = screen.getByRole("button", { name: "Run" });
    expect(runButton).toHaveProperty("disabled", true);
    fireEvent.click(runButton);
    expect(runPostCalls()).toHaveLength(0);
  });

  it("does not carry prior selections into a deal whose catalog lacks those records", async () => {
    const view = render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd, { target: { value: CDD_A } });
    fireEvent.change(fdd, { target: { value: FDD_A } });
    expect(screen.getByRole("button", { name: "Run" })).toHaveProperty("disabled", false);

    catalogHandler = () =>
      jsonResponse(200, [
        methodologyRecord(CDD_B, "commercial_dd", "CDD v1.0", "FROZEN"),
        methodologyRecord(FDD_B, "financial_dd", "FDD v3.5", "FROZEN"),
      ]);
    nav.dealId = "deal-2";
    view.rerender(<TruthDashboardPage />);
    await screen.findByText(DEAL_NAME_PREFIX + "deal-2");
    await screen.findByRole("option", { name: /CDD v1\.0/ });

    const cdd2 = screen.getByLabelText(/commercial dd methodology/i);
    const fdd2 = screen.getByLabelText(/financial dd methodology/i);
    expect(cdd2).toHaveProperty("value", "");
    expect(fdd2).toHaveProperty("value", "");
    expect(screen.queryByRole("option", { name: /CDD v2\.1/ })).toBeNull();

    const runButton = screen.getByRole("button", { name: "Run" });
    expect(runButton).toHaveProperty("disabled", true);
    fireEvent.click(runButton);
    expect(runPostCalls()).toHaveLength(0);
  });

  it("shows a 400 error with its request id and does not retry or navigate", async () => {
    runHandler = () =>
      jsonResponse(400, {
        code: "VALIDATION_FAILED",
        message: "Run request validation failed",
        request_id: "req-400-3",
      });

    render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd, { target: { value: CDD_A } });
    fireEvent.change(fdd, { target: { value: FDD_A } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));

    await screen.findByText("Run request validation failed");
    await screen.findByText(/req-400-3/);
    expect(nav.router.push).not.toHaveBeenCalled();
    expect(runPostCalls()).toHaveLength(1);
  });

  it("blocks launch until the current deal's loaded data arrives even when its catalog resolved first", async () => {
    const view = render(<TruthDashboardPage />);
    await screen.findByText("Total Claims");
    await screen.findByLabelText(/commercial dd methodology/i);

    const gate = deferred<void>();
    dataGate = gate.promise;
    nav.dealId = "deal-2";
    view.rerender(<TruthDashboardPage />);

    // deal-2's catalog arrives while its deal data is still in flight; the
    // displayed subject is still deal-1, so no pair may launch yet.
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd, { target: { value: CDD_B } });
    fireEvent.change(fdd, { target: { value: FDD_B } });

    const runButton = screen.getByRole("button", { name: "Run" });
    expect(runButton).toHaveProperty("disabled", true);
    fireEvent.click(runButton);
    expect(runPostCalls()).toHaveLength(0);

    await act(async () => {
      gate.resolve();
    });
    await screen.findByText(DEAL_NAME_PREFIX + "deal-2");

    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(runPostCalls()).toHaveLength(1));
    const [url, init] = runPostCalls()[0];
    expect(String(url)).toBe("/api/idis/v1/deals/deal-2/runs");
    expect(JSON.parse(String(init?.body))).toEqual({
      mode: "FULL",
      methodology_selection: {
        commercial_dd_version_record_id: CDD_B,
        financial_dd_version_record_id: FDD_B,
      },
    });
  });

  it("does not navigate or mutate the current deal when a superseded launch resolves after a deal change", async () => {
    const staleRun = deferred<Response>();
    runHandler = () => staleRun.promise;

    const view = render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd, { target: { value: CDD_A } });
    fireEvent.change(fdd, { target: { value: FDD_A } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(runPostCalls()).toHaveLength(1));

    nav.dealId = "deal-2";
    view.rerender(<TruthDashboardPage />);
    await screen.findByText(DEAL_NAME_PREFIX + "deal-2");
    await screen.findByLabelText(/commercial dd methodology/i);

    await act(async () => {
      staleRun.resolve(jsonResponse(202, { run_id: "run-stale-a", status: "QUEUED" }));
    });

    expect(nav.router.push).not.toHaveBeenCalled();

    // The current deal's own launch still works and navigates exactly once.
    runHandler = () => jsonResponse(202, { run_id: "run-fixed-1", status: "QUEUED" });
    const cdd2 = screen.getByLabelText(/commercial dd methodology/i);
    const fdd2 = screen.getByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd2, { target: { value: CDD_B } });
    fireEvent.change(fdd2, { target: { value: FDD_B } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(runPostCalls()).toHaveLength(2));
    await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith("/runs/run-fixed-1"));
    expect(nav.router.push).toHaveBeenCalledTimes(1);
  });

  it("does not navigate when a pending launch resolves after unmount", async () => {
    const staleRun = deferred<Response>();
    runHandler = () => staleRun.promise;

    const view = render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd, { target: { value: CDD_A } });
    fireEvent.change(fdd, { target: { value: FDD_A } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(runPostCalls()).toHaveLength(1));

    view.unmount();
    await act(async () => {
      staleRun.resolve(jsonResponse(202, { run_id: "run-stale-a", status: "QUEUED" }));
    });

    expect(nav.router.push).not.toHaveBeenCalled();
  });

  it("discards a superseded launch failure without showing its error or logging out", async () => {
    const staleRun = deferred<Response>();
    runHandler = () => staleRun.promise;

    const view = render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd, { target: { value: CDD_A } });
    fireEvent.change(fdd, { target: { value: FDD_A } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(runPostCalls()).toHaveLength(1));

    nav.dealId = "deal-2";
    view.rerender(<TruthDashboardPage />);
    await screen.findByText(DEAL_NAME_PREFIX + "deal-2");

    await act(async () => {
      staleRun.resolve(
        jsonResponse(409, {
          code: "SUPERSEDED",
          message: "Superseded conflict",
          request_id: "req-stale-4",
        }),
      );
    });

    expect(screen.queryByText("Superseded conflict")).toBeNull();
    expect(screen.queryByText(/req-stale-4/)).toBeNull();
    expect(nav.router.push).not.toHaveBeenCalled();
  });

  it("does not redirect to login for a superseded launch 401", async () => {
    const staleRun = deferred<Response>();
    runHandler = () => staleRun.promise;

    const view = render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd, { target: { value: CDD_A } });
    fireEvent.change(fdd, { target: { value: FDD_A } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(runPostCalls()).toHaveLength(1));

    nav.dealId = "deal-2";
    view.rerender(<TruthDashboardPage />);
    await screen.findByText(DEAL_NAME_PREFIX + "deal-2");

    await act(async () => {
      staleRun.resolve(
        jsonResponse(401, { code: "unauthorized", message: "Not authenticated" }),
      );
    });

    expect(nav.router.push).not.toHaveBeenCalled();
  });

  it("a superseded pending launch cannot unlock the current deal's pending launch", async () => {
    const staleRun = deferred<Response>();
    const currentRun = deferred<Response>();
    const pendingRuns = [staleRun, currentRun];
    let runIndex = 0;
    runHandler = () => pendingRuns[runIndex++].promise;

    const view = render(<TruthDashboardPage />);
    const cdd = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd = await screen.findByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd, { target: { value: CDD_A } });
    fireEvent.change(fdd, { target: { value: FDD_A } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(runPostCalls()).toHaveLength(1));

    nav.dealId = "deal-2";
    view.rerender(<TruthDashboardPage />);
    await screen.findByText(DEAL_NAME_PREFIX + "deal-2");
    const cdd2 = await screen.findByLabelText(/commercial dd methodology/i);
    const fdd2 = await screen.findByLabelText(/financial dd methodology/i);
    fireEvent.change(cdd2, { target: { value: CDD_B } });
    fireEvent.change(fdd2, { target: { value: FDD_B } });
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(runPostCalls()).toHaveLength(2));
    await screen.findByRole("button", { name: "..." });

    // The superseded request resolves late: it must not navigate, install
    // error state, or release the current deal's pending launch.
    await act(async () => {
      staleRun.resolve(jsonResponse(202, { run_id: "run-stale-a", status: "QUEUED" }));
    });

    expect(nav.router.push).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "..." })).toHaveProperty("disabled", true);

    await act(async () => {
      currentRun.resolve(jsonResponse(202, { run_id: "run-b-7", status: "QUEUED" }));
    });
    expect(nav.router.push).toHaveBeenCalledTimes(1);
    expect(nav.router.push).toHaveBeenCalledWith("/runs/run-b-7");

    const posts = runPostCalls();
    expect(String(posts[0][0])).toBe("/api/idis/v1/deals/deal-1/runs");
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual({
      mode: "FULL",
      methodology_selection: {
        commercial_dd_version_record_id: CDD_A,
        financial_dd_version_record_id: FDD_A,
      },
    });
    expect(String(posts[1][0])).toBe("/api/idis/v1/deals/deal-2/runs");
    expect(JSON.parse(String(posts[1][1]?.body))).toEqual({
      mode: "FULL",
      methodology_selection: {
        commercial_dd_version_record_id: CDD_B,
        financial_dd_version_record_id: FDD_B,
      },
    });
  });

  it("invalidates a superseded launch at the committed deal change before passive cleanup", async () => {
    const actEnv = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    const previousActEnv = actEnv.IS_REACT_ACT_ENVIRONMENT;
    actEnv.IS_REACT_ACT_ENVIRONMENT = false;
    const staleRun = deferred<Response>();
    runHandler = () => staleRun.promise;

    const { container, root } = renderCommittedPage();
    try {
      const cdd = await screen.findByLabelText(/commercial dd methodology/i);
      const fdd = await screen.findByLabelText(/financial dd methodology/i);
      fireEvent.change(cdd, { target: { value: CDD_A } });
      fireEvent.change(fdd, { target: { value: FDD_A } });
      fireEvent.click(screen.getByRole("button", { name: "Run" }));
      await waitFor(() => expect(runPostCalls()).toHaveLength(1));

      const deal2Calls = () =>
        fetchMock.mock.calls.filter((call) => String(call[0]).includes("deal-2"));
      let deal2CallsAtSettle = -1;

      // The queued commit task commits deal-2 first; the busy task pushes
      // the burst past its yield deadline so the commit's queued passive
      // effects are deferred to a later burst, and the settle task resolves
      // the pending launch inside the commit-before-passive window only.
      nav.dealId = "deal-2";
      queueBoundaryCommit(
        () => {
          root.render(<TruthDashboardPage />);
        },
        () => {
          deal2CallsAtSettle = deal2Calls().length;
          staleRun.resolve(jsonResponse(202, { run_id: "run-stale-a", status: "QUEUED" }));
        },
      );

      // Deal-2's deferred passive effects do eventually run, proving the
      // boundary commit ran first; at settle time no deal-2 request
      // existed yet — the settle sat inside the window.
      await waitFor(() => expect(deal2Calls().length).toBeGreaterThan(0));
      expect(deal2CallsAtSettle).toBe(0);
      expect(nav.router.push).not.toHaveBeenCalled();
      expect(screen.queryByText("Failed to start run")).toBeNull();

      // The current subject still owns the page once its passive work runs:
      // deal-2 loads and its own launch posts its own URL/body and navigates.
      runHandler = () => jsonResponse(202, { run_id: "run-b-9", status: "QUEUED" });
      await screen.findByText(DEAL_NAME_PREFIX + "deal-2");
      const cdd2 = await screen.findByLabelText(/commercial dd methodology/i);
      const fdd2 = await screen.findByLabelText(/financial dd methodology/i);
      fireEvent.change(cdd2, { target: { value: CDD_B } });
      fireEvent.change(fdd2, { target: { value: FDD_B } });
      fireEvent.click(screen.getByRole("button", { name: "Run" }));
      await waitFor(() => expect(runPostCalls()).toHaveLength(2));
      await waitFor(() => expect(nav.router.push).toHaveBeenCalledTimes(1));
      expect(nav.router.push).toHaveBeenCalledWith("/runs/run-b-9");
      const posts = runPostCalls();
      expect(String(posts[0][0])).toBe("/api/idis/v1/deals/deal-1/runs");
      expect(JSON.parse(String(posts[0][1]?.body))).toEqual({
        mode: "FULL",
        methodology_selection: {
          commercial_dd_version_record_id: CDD_A,
          financial_dd_version_record_id: FDD_A,
        },
      });
      expect(String(posts[1][0])).toBe("/api/idis/v1/deals/deal-2/runs");
      expect(JSON.parse(String(posts[1][1]?.body))).toEqual({
        mode: "FULL",
        methodology_selection: {
          commercial_dd_version_record_id: CDD_B,
          financial_dd_version_record_id: FDD_B,
        },
      });
    } finally {
      actEnv.IS_REACT_ACT_ENVIRONMENT = previousActEnv;
      root.unmount();
      container.remove();
    }
  });

  it("does not redirect to login for a pending launch 401 settling in the committed deal-change window", async () => {
    const actEnv = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    const previousActEnv = actEnv.IS_REACT_ACT_ENVIRONMENT;
    actEnv.IS_REACT_ACT_ENVIRONMENT = false;
    const staleRun = deferred<Response>();
    runHandler = () => staleRun.promise;

    const { container, root } = renderCommittedPage();
    try {
      const cdd = await screen.findByLabelText(/commercial dd methodology/i);
      const fdd = await screen.findByLabelText(/financial dd methodology/i);
      fireEvent.change(cdd, { target: { value: CDD_A } });
      fireEvent.change(fdd, { target: { value: FDD_A } });
      fireEvent.click(screen.getByRole("button", { name: "Run" }));
      await waitFor(() => expect(runPostCalls()).toHaveLength(1));

      const deal2Calls = () =>
        fetchMock.mock.calls.filter((call) => String(call[0]).includes("deal-2"));
      let deal2CallsAtSettle = -1;

      nav.dealId = "deal-2";
      queueBoundaryCommit(
        () => {
          root.render(<TruthDashboardPage />);
        },
        () => {
          deal2CallsAtSettle = deal2Calls().length;
          staleRun.resolve(
            jsonResponse(401, { code: "unauthorized", message: "Not authenticated" }),
          );
        },
      );

      // The superseded 401 settles inside the commit-before-passive
      // window: at settle time no deal-2 request existed yet.
      await waitFor(() => expect(deal2Calls().length).toBeGreaterThan(0));
      expect(deal2CallsAtSettle).toBe(0);
      expect(nav.router.push).not.toHaveBeenCalled();
      expect(screen.queryByText("Failed to start run")).toBeNull();
    } finally {
      actEnv.IS_REACT_ACT_ENVIRONMENT = previousActEnv;
      root.unmount();
      container.remove();
    }
  });

  it("does not navigate when a pending launch resolves after the unmount commit before passive cleanup", async () => {
    const actEnv = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    const previousActEnv = actEnv.IS_REACT_ACT_ENVIRONMENT;
    actEnv.IS_REACT_ACT_ENVIRONMENT = false;
    const staleRun = deferred<Response>();
    runHandler = () => staleRun.promise;

    const { container, root } = renderCommittedPage();
    let unmounted = false;
    try {
      const cdd = await screen.findByLabelText(/commercial dd methodology/i);
      const fdd = await screen.findByLabelText(/financial dd methodology/i);
      fireEvent.change(cdd, { target: { value: CDD_A } });
      fireEvent.change(fdd, { target: { value: FDD_A } });
      fireEvent.click(screen.getByRole("button", { name: "Run" }));
      await waitFor(() => expect(runPostCalls()).toHaveLength(1));

      // The removal commits in the first queued task while the removed
      // subject's passive cleanups are deferred past the yield deadline;
      // the settle task resolves the launch inside the committed-but-not-
      // passive interval, and its continuations run at the burst boundary.
      let settleRan = false;
      queueBoundaryCommit(
        () => {
          root.render(<div />);
        },
        () => {
          settleRan = true;
          staleRun.resolve(jsonResponse(202, { run_id: "run-stale-a", status: "QUEUED" }));
        },
      );

      await waitFor(() => expect(settleRan).toBe(true));
      unmounted = true;
      await drainMicrotasks();

      expect(nav.router.push).not.toHaveBeenCalled();
    } finally {
      actEnv.IS_REACT_ACT_ENVIRONMENT = previousActEnv;
      if (!unmounted) root.unmount();
      container.remove();
    }
  });
});
