"use client";

import { useEffect, useState, useCallback } from "react";
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
  type SanadGrade,
  IDISApiError,
} from "@/lib/idis";
import { generateRequestId } from "@/lib/requestId";

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

  const fetchData = useCallback(async () => {
    try {
      const [dealData, dashboardData, deliverablesData, gatesData] = await Promise.all([
        idis.deals.get(dealId),
        idis.deals.getTruthDashboard(dealId, { limit: 100 }),
        idis.deliverables.list(dealId, { limit: 50 }),
        idis.humanGates.list(dealId, { limit: 50 }),
      ]);

      setDeal(dealData);
      setDashboard(dashboardData);

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
      setLoading(false);
    }
  }, [dealId, router]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  async function handleStartRun() {
    setActionLoading("run");
    try {
      const result = await idis.runs.start(dealId, { mode: "FULL" }, generateRequestId());
      router.push(`/runs/${result.run_id}`);
    } catch (err) {
      if (err instanceof IDISApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      alert(err instanceof Error ? err.message : "Failed to start run");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleStartDebate() {
    setActionLoading("debate");
    try {
      const result = await idis.debate.start(
        dealId,
        { protocol_version: "v1" },
        generateRequestId()
      );
      router.push(`/runs/${result.run_id}`);
    } catch (err) {
      if (err instanceof IDISApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      alert(err instanceof Error ? err.message : "Failed to start debate");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleGenerateDeliverable(type: string) {
    setActionLoading(`deliverable-${type}`);
    try {
      await idis.deliverables.generate(dealId, { deliverable_type: type }, generateRequestId());
      await fetchData(); // Refresh data
    } catch (err) {
      if (err instanceof IDISApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      alert(err instanceof Error ? err.message : "Failed to generate deliverable");
    } finally {
      setActionLoading(null);
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
                disabled={actionLoading !== null}
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
