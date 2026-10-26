"use client";

import { useEffect, useLayoutEffect, useState, useCallback, useRef } from "react";
import { useRouter, useParams } from "next/navigation";
import Link from "next/link";
import Header from "@/components/Header";
import GradeBadge from "@/components/GradeBadge";
import VerdictBadge from "@/components/VerdictBadge";
import StatusBadge from "@/components/StatusBadge";
import ErrorCallout from "@/components/ErrorCallout";
import {
  idis,
  type Deal,
  type TruthDashboard,
  type Deliverable,
  type HumanGate,
  type MethodologyVersionSafe,
  type SanadGrade,
  IDISApiError,
} from "@/lib/idis";
import { generateRequestId } from "@/lib/requestId";

// Commit-synchronous effect: its create and cleanup run during the commit
// phase, before queued passive work and before any promise continuation can
// interleave. Falls back to useEffect on the server, where no commit exists.
const useCommitPhaseEffect: typeof useEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

export default function TruthDashboardPage() {
  const router = useRouter();
  const params = useParams();
  const dealId = params.dealId as string;

  const [deal, setDeal] = useState<Deal | null>(null);
  const [dashboard, setDashboard] = useState<TruthDashboard | null>(null);
  const [deliverables, setDeliverables] = useState<Deliverable[]>([]);
  const [humanGates, setHumanGates] = useState<HumanGate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = useState<string | undefined>(undefined);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [loadedDealId, setLoadedDealId] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<{
    dealId: string;
    versions: MethodologyVersionSafe[];
  } | null>(null);
  const [methodologyLoading, setMethodologyLoading] = useState(true);
  const [methodologyError, setMethodologyError] = useState<string | null>(null);
  const [methodologyErrorRequestId, setMethodologyErrorRequestId] = useState<
    string | undefined
  >(undefined);
  const [cddSelection, setCddSelection] = useState("");
  const [fddSelection, setFddSelection] = useState("");
  const [runError, setRunError] = useState<string | null>(null);
  const [runErrorRequestId, setRunErrorRequestId] = useState<string | undefined>(undefined);

  // Live launch-subject generation. Bumped whenever the current deal changes or
  // the page unmounts, so the terminal effects of async work started for a
  // superseded subject cannot commit state, navigate, or install errors.
  const launchGenRef = useRef(0);

  const fetchData = useCallback(async () => {
    const gen = launchGenRef.current;
    try {
      const [dealData, dashboardData, deliverablesData, gatesData] = await Promise.all([
        idis.deals.get(dealId),
        idis.deals.getTruthDashboard(dealId, { limit: 100 }),
        idis.deliverables.list(dealId, { limit: 50 }),
        idis.humanGates.list(dealId, { limit: 50 }),
      ]);

      if (gen !== launchGenRef.current) return;
      setDeal(dealData);
      setDashboard(dashboardData);
      setLoadedDealId(dealId);

      // Deterministic sorting for deliverables
      const sortedDeliverables = [...deliverablesData.items].sort((a, b) => {
        const dateCompare = new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
        if (dateCompare !== 0) return dateCompare;
        return a.deliverable_id.localeCompare(b.deliverable_id);
      });
      setDeliverables(sortedDeliverables);

      // Deterministic sorting for human gates
      const sortedGates = [...gatesData.items].sort((a, b) => {
        const dateCompare = new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
        if (dateCompare !== 0) return dateCompare;
        return a.gate_id.localeCompare(b.gate_id);
      });
      setHumanGates(sortedGates);
    } catch (err) {
      if (gen !== launchGenRef.current) return;
      if (err instanceof IDISApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      if (err instanceof IDISApiError) {
        setError(err.message);
        setErrorRequestId(err.requestId);
      } else {
        setError(err instanceof Error ? err.message : "Failed to load data");
      }
    } finally {
      if (gen === launchGenRef.current) setLoading(false);
    }
  }, [dealId, router]);

  // Generation invalidation is commit-synchronous so a stale continuation of a
  // superseded launch can never run in the commit-before-passive interval. No
  // state update may live here: a layout-phase setState forces a sync re-render
  // whose work entry flushes this commit's pending passive effects early.
  useCommitPhaseEffect(() => {
    launchGenRef.current += 1;
    return () => {
      launchGenRef.current += 1;
    };
  }, [dealId]);

  useEffect(() => {
    // A pending operation belongs to the superseded subject; its stale finally
    // is guarded, so release the current page's operation state here instead.
    setActionLoading(null);
  }, [dealId]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Load the tenant methodology catalog independently of the dashboard so a
  // catalog failure cannot erase already-loaded deal content. The effect is
  // keyed on dealId; the committed catalog is owned by the deal it was loaded
  // for, and a stale or out-of-order response for a previous generation is
  // discarded instead of reinstalling old choices.
  useEffect(() => {
    const gen = launchGenRef.current;
    setCddSelection("");
    setFddSelection("");
    setRunError(null);
    setRunErrorRequestId(undefined);
    setCatalog(null);
    setMethodologyLoading(true);
    setMethodologyError(null);
    setMethodologyErrorRequestId(undefined);
    idis.methodology
      .list()
      .then((versions) => {
        if (gen !== launchGenRef.current) return;
        setCatalog({ dealId, versions });
      })
      .catch((err: unknown) => {
        if (gen !== launchGenRef.current) return;
        if (err instanceof IDISApiError && err.status === 401) {
          router.push("/login");
          return;
        }
        if (err instanceof IDISApiError) {
          setMethodologyError(err.message);
          setMethodologyErrorRequestId(err.requestId);
        } else {
          setMethodologyError(
            err instanceof Error ? err.message : "Failed to load methodology versions"
          );
        }
      })
      .finally(() => {
        if (gen === launchGenRef.current) setMethodologyLoading(false);
      });
  }, [dealId, router]);

  // Launch readiness is owned by the current deal and generation: the loaded
  // subject and the ready catalog must both belong to this deal, and the
  // explicit pair must be distinct records from the eligible FROZEN families.
  const loadedSubjectIsCurrent = loadedDealId === dealId;
  const catalogIsCurrent = catalog !== null && catalog.dealId === dealId;
  const catalogVersions = catalogIsCurrent ? catalog.versions : [];
  const frozenCdd = catalogVersions.filter(
    (v) => v.methodology_type === "commercial_dd" && v.status === "FROZEN"
  );
  const frozenFdd = catalogVersions.filter(
    (v) => v.methodology_type === "financial_dd" && v.status === "FROZEN"
  );
  const methodologySelected =
    loadedSubjectIsCurrent &&
    catalogIsCurrent &&
    cddSelection !== "" &&
    fddSelection !== "" &&
    cddSelection !== fddSelection &&
    frozenCdd.some((v) => v.methodology_version_record_id === cddSelection) &&
    frozenFdd.some((v) => v.methodology_version_record_id === fddSelection);

  async function handleStartRun() {
    if (actionLoading !== null || !methodologySelected) return;
    const gen = launchGenRef.current;
    const commercialId = cddSelection;
    const financialId = fddSelection;

    setActionLoading("run");
    setRunError(null);
    setRunErrorRequestId(undefined);
    try {
      const result = await idis.runs.start(
        dealId,
        {
          mode: "FULL",
          methodology_selection: {
            commercial_dd_version_record_id: commercialId,
            financial_dd_version_record_id: financialId,
          },
        },
        generateRequestId()
      );
      if (gen !== launchGenRef.current) return;
      router.push(`/runs/${result.run_id}`);
    } catch (err) {
      if (gen !== launchGenRef.current) return;
      if (err instanceof IDISApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      if (err instanceof IDISApiError) {
        setRunError(err.message);
        setRunErrorRequestId(err.requestId);
      } else {
        alert(err instanceof Error ? err.message : "Failed to start run");
      }
    } finally {
      if (gen === launchGenRef.current) setActionLoading(null);
    }
  }

  async function handleStartDebate() {
    const gen = launchGenRef.current;
    setActionLoading("debate");
    try {
      const result = await idis.debate.start(
        dealId,
        { protocol_version: "v1" },
        generateRequestId()
      );
      if (gen !== launchGenRef.current) return;
      router.push(`/runs/${result.run_id}`);
    } catch (err) {
      if (gen !== launchGenRef.current) return;
      if (err instanceof IDISApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      alert(err instanceof Error ? err.message : "Failed to start debate");
    } finally {
      if (gen === launchGenRef.current) setActionLoading(null);
    }
  }

  async function handleGenerateDeliverable(type: string) {
    const gen = launchGenRef.current;
    setActionLoading(`deliverable-${type}`);
    try {
      await idis.deliverables.generate(dealId, { deliverable_type: type }, generateRequestId());
      if (gen === launchGenRef.current) await fetchData(); // Refresh data
    } catch (err) {
      if (gen !== launchGenRef.current) return;
      if (err instanceof IDISApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      alert(err instanceof Error ? err.message : "Failed to generate deliverable");
    } finally {
      if (gen === launchGenRef.current) setActionLoading(null);
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <main className="max-w-7xl mx-auto px-4 py-8">
          <div className="text-center text-gray-500">Loading dashboard...</div>
        </main>
      </div>
    );
  }

  if (error || !deal || !dashboard) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <main className="max-w-7xl mx-auto px-4 py-8">
          <ErrorCallout message={error || "Deal not found"} requestId={errorRequestId} />
        </main>
      </div>
    );
  }

  const { summary, claims } = dashboard;

  // Deterministic sorting of claims
  const sortedClaims = [...claims.items].sort((a, b) => {
    const dateCompare = new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    if (dateCompare !== 0) return dateCompare;
    return a.claim_id.localeCompare(b.claim_id);
  });

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="max-w-7xl mx-auto px-4 py-8">
        {/* Deal Header */}
        <div className="mb-8">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-gray-900">{deal.name}</h1>
              <p className="text-gray-600">{deal.company_name}</p>
            </div>
            <StatusBadge status={deal.status} type="deal" />
          </div>
        </div>

        {/* Summary Cards */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
          <div className="bg-white p-4 rounded-lg shadow">
            <h3 className="text-sm font-medium text-gray-500">Total Claims</h3>
            <p className="text-2xl font-bold text-gray-900">{summary.total_claims}</p>
          </div>
          <div className="bg-white p-4 rounded-lg shadow">
            <h3 className="text-sm font-medium text-gray-500">Grade Distribution</h3>
            <div className="flex gap-2 mt-1">
              {(["A", "B", "C", "D"] as SanadGrade[]).map((grade) => (
                <span key={grade} className="text-sm">
                  <GradeBadge grade={grade} showTooltip={false} />
                  <span className="ml-1 text-gray-600">{summary.by_grade[grade] || 0}</span>
                </span>
              ))}
            </div>
          </div>
          <div className="bg-white p-4 rounded-lg shadow">
            <h3 className="text-sm font-medium text-gray-500">Fatal Defects</h3>
            <p className={`text-2xl font-bold ${summary.fatal_defects > 0 ? "text-red-600" : "text-green-600"}`}>
              {summary.fatal_defects}
            </p>
          </div>
          <div className="bg-white p-4 rounded-lg shadow">
            <h3 className="text-sm font-medium text-gray-500">Actions</h3>
            <div className="flex gap-2 mt-1">
              <button
                onClick={handleStartRun}
                disabled={actionLoading !== null || !methodologySelected}
                className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {actionLoading === "run" ? "..." : "Run"}
              </button>
              <button
                onClick={handleStartDebate}
                disabled={actionLoading !== null}
                className="px-2 py-1 text-xs bg-purple-600 text-white rounded hover:bg-purple-700 disabled:opacity-50"
              >
                {actionLoading === "debate" ? "..." : "Debate"}
              </button>
            </div>
          </div>
        </div>

        {/* FULL run methodology selection */}
        <div className="bg-white shadow rounded-lg mb-8">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-medium text-gray-900">Run Methodology</h2>
          </div>
          <div className="p-6">
            <p className="text-sm text-gray-500 mb-4">
              A FULL run requires explicit frozen commercial and financial methodology versions.
            </p>
            {methodologyLoading ? (
              <div className="text-sm text-gray-500">Loading methodology versions...</div>
            ) : methodologyError ? (
              <ErrorCallout
                title="Methodology catalog unavailable"
                message={methodologyError}
                requestId={methodologyErrorRequestId}
              />
            ) : !catalogIsCurrent ? (
              <div className="text-sm text-gray-500">Loading methodology versions...</div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label
                    htmlFor="cdd-methodology"
                    className="block text-sm font-medium text-gray-700"
                  >
                    Commercial DD methodology (FROZEN)
                  </label>
                  <select
                    id="cdd-methodology"
                    value={cddSelection}
                    onChange={(e) => setCddSelection(e.target.value)}
                    disabled={actionLoading !== null}
                    className="mt-1 block w-full rounded border border-gray-300 px-3 py-2 text-sm"
                  >
                    <option value="">Select frozen commercial DD version</option>
                    {frozenCdd.map((v) => (
                      <option
                        key={v.methodology_version_record_id}
                        value={v.methodology_version_record_id}
                      >
                        {v.version_label} ({v.methodology_version_id})
                      </option>
                    ))}
                  </select>
                  {frozenCdd.length === 0 && (
                    <p className="mt-1 text-xs text-gray-500">
                      No frozen commercial DD versions available
                    </p>
                  )}
                </div>
                <div>
                  <label
                    htmlFor="fdd-methodology"
                    className="block text-sm font-medium text-gray-700"
                  >
                    Financial DD methodology (FROZEN)
                  </label>
                  <select
                    id="fdd-methodology"
                    value={fddSelection}
                    onChange={(e) => setFddSelection(e.target.value)}
                    disabled={actionLoading !== null}
                    className="mt-1 block w-full rounded border border-gray-300 px-3 py-2 text-sm"
                  >
                    <option value="">Select frozen financial DD version</option>
                    {frozenFdd.map((v) => (
                      <option
                        key={v.methodology_version_record_id}
                        value={v.methodology_version_record_id}
                      >
                        {v.version_label} ({v.methodology_version_id})
                      </option>
                    ))}
                  </select>
                  {frozenFdd.length === 0 && (
                    <p className="mt-1 text-xs text-gray-500">
                      No frozen financial DD versions available
                    </p>
                  )}
                </div>
              </div>
            )}
            {runError !== null && (
              <div className="mt-4">
                <ErrorCallout
                  title="Failed to start run"
                  message={runError}
                  requestId={runErrorRequestId}
                />
              </div>
            )}
          </div>
        </div>

        {/* Fatal Defect Banner */}
        {summary.fatal_defects > 0 && (
          <div className="mb-6 rounded-md bg-red-50 border border-red-200 p-4">
            <p className="text-sm text-red-700 font-medium">
              ⚠️ This deal has {summary.fatal_defects} fatal defect(s). IC Ready export is blocked until cured/waived.
            </p>
          </div>
        )}

        {/* Claims Table */}
        <div className="bg-white shadow rounded-lg mb-8">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-medium text-gray-900">Claims ({sortedClaims.length})</h2>
          </div>
          {sortedClaims.length === 0 ? (
            <div className="p-6 text-center text-gray-500">No claims found</div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Claim</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Verdict</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Grade</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Corroboration</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {sortedClaims.map((claim) => (
                  <tr key={claim.claim_id} className="hover:bg-gray-50">
                    <td className="px-6 py-4">
                      <Link
                        href={`/claims/${claim.claim_id}`}
                        className="text-blue-600 hover:text-blue-800"
                      >
                        {claim.claim_text.length > 80
                          ? claim.claim_text.substring(0, 80) + "..."
                          : claim.claim_text}
                      </Link>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {claim.claim_class}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <VerdictBadge verdict={claim.claim_verdict} />
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <GradeBadge grade={claim.claim_grade} />
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {claim.corroboration.level} ({claim.corroboration.independent_chain_count})
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Deliverables Section */}
        <div className="bg-white shadow rounded-lg mb-8">
          <div className="px-6 py-4 border-b border-gray-200 flex justify-between items-center">
            <h2 className="text-lg font-medium text-gray-900">Deliverables</h2>
            <div className="flex gap-2">
              <Link
                href={`/deals/${dealId}/deliverables`}
                className="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
              >
                View All
              </Link>
              <button
                onClick={() => handleGenerateDeliverable("screening_snapshot")}
                disabled={actionLoading !== null}
                className="px-3 py-1 text-sm bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-50"
              >
                {actionLoading === "deliverable-screening_snapshot" ? "..." : "+ Snapshot"}
              </button>
              <button
                onClick={() => handleGenerateDeliverable("ic_memo")}
                disabled={actionLoading !== null}
                className="px-3 py-1 text-sm bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-50"
              >
                {actionLoading === "deliverable-ic_memo" ? "..." : "+ IC Memo"}
              </button>
            </div>
          </div>
          {deliverables.length === 0 ? (
            <div className="p-6 text-center text-gray-500">No deliverables generated yet</div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Download</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {deliverables.map((d) => (
                  <tr key={d.deliverable_id}>
                    <td className="px-6 py-4 text-sm text-gray-900">{d.deliverable_type}</td>
                    <td className="px-6 py-4">
                      <StatusBadge status={d.status} type="run" />
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {new Date(d.created_at).toLocaleString()}
                    </td>
                    <td className="px-6 py-4 text-sm">
                      {d.uri ? (
                        <a href={d.uri} className="text-blue-600 hover:text-blue-800">
                          Download
                        </a>
                      ) : (
                        <span className="text-gray-400">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Human Gates Section */}
        <div className="bg-white shadow rounded-lg">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-medium text-gray-900">Human Verification Gates</h2>
          </div>
          {humanGates.length === 0 ? (
            <div className="p-6 text-center text-gray-500">No verification gates pending</div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Gate ID</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Created</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {humanGates.map((gate) => (
                  <tr key={gate.gate_id}>
                    <td className="px-6 py-4 text-sm font-mono text-gray-700">
                      {gate.gate_id.substring(0, 8)}...
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-900">{gate.gate_type}</td>
                    <td className="px-6 py-4">
                      <StatusBadge status={gate.status} type="run" />
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {new Date(gate.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  );
}
