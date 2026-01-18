"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter, useParams } from "next/navigation";
import Link from "next/link";
import Header from "@/components/Header";
import GradeBadge from "@/components/GradeBadge";
import VerdictBadge from "@/components/VerdictBadge";
import { idis, type Claim, type Sanad, IDISApiError } from "@/lib/idis";

export default function ClaimDetailPage() {
  const router = useRouter();
  const params = useParams();
  const claimId = params.claimId as string;

  const [claim, setClaim] = useState<Claim | null>(null);
  const [sanad, setSanad] = useState<Sanad | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [claimData, sanadData] = await Promise.all([
        idis.claims.get(claimId),
        idis.claims.getSanad(claimId).catch(() => null), // Sanad may not exist
      ]);
      setClaim(claimData);
      setSanad(sanadData);
    } catch (err) {
      if (err instanceof IDISApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "Failed to load claim");
    } finally {
      setLoading(false);
    }
  }, [claimId, router]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <main className="max-w-4xl mx-auto px-4 py-8">
          <div className="text-center text-gray-500">Loading claim...</div>
        </main>
      </div>
    );
  }

  if (error || !claim) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <main className="max-w-4xl mx-auto px-4 py-8">
          <div className="rounded-md bg-red-50 p-4">
            <p className="text-sm text-red-700">{error || "Claim not found"}</p>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="max-w-4xl mx-auto px-4 py-8">
        {/* Breadcrumb */}
        <nav className="mb-4 text-sm">
          <Link href="/deals" className="text-blue-600 hover:text-blue-800">
            Deals
          </Link>
          <span className="mx-2 text-gray-400">/</span>
          <Link
            href={`/deals/${claim.deal_id}/truth-dashboard`}
            className="text-blue-600 hover:text-blue-800"
          >
            Deal Dashboard
          </Link>
          <span className="mx-2 text-gray-400">/</span>
          <span className="text-gray-600">Claim</span>
        </nav>

        {/* Claim Header */}
        <div className="bg-white shadow rounded-lg p-6 mb-6">
          <div className="flex items-start justify-between mb-4">
            <div className="flex-1">
              <h1 className="text-xl font-bold text-gray-900 mb-2">{claim.claim_text}</h1>
              <div className="flex items-center gap-3">
                <VerdictBadge verdict={claim.claim_verdict} />
                <GradeBadge grade={claim.claim_grade} />
                <span className="text-sm text-gray-500">
                  {claim.corroboration.level} ({claim.corroboration.independent_chain_count} chains)
                </span>
              </div>
            </div>
          </div>

          {/* Claim Details Grid */}
          <div className="grid grid-cols-2 gap-4 mt-6 pt-6 border-t border-gray-200">
            <div>
              <dt className="text-sm font-medium text-gray-500">Class</dt>
              <dd className="mt-1 text-sm text-gray-900">{claim.claim_class}</dd>
            </div>
            <div>
              <dt className="text-sm font-medium text-gray-500">Action</dt>
              <dd className="mt-1 text-sm text-gray-900">
                {claim.claim_action.replace(/_/g, " ")}
              </dd>
            </div>
            {claim.materiality && (
              <div>
                <dt className="text-sm font-medium text-gray-500">Materiality</dt>
                <dd className="mt-1 text-sm text-gray-900">{claim.materiality}</dd>
              </div>
            )}
            {claim.ic_bound !== undefined && (
              <div>
                <dt className="text-sm font-medium text-gray-500">IC Bound</dt>
                <dd className="mt-1 text-sm text-gray-900">{claim.ic_bound ? "Yes" : "No"}</dd>
              </div>
            )}
            {claim.value && (
              <div>
                <dt className="text-sm font-medium text-gray-500">Value</dt>
                <dd className="mt-1 text-sm text-gray-900">
                  {claim.value.currency && `${claim.value.currency} `}
                  {claim.value.value.toLocaleString()} {claim.value.unit}
                  {claim.value.as_of && ` (as of ${claim.value.as_of})`}
                </dd>
              </div>
            )}
            <div>
              <dt className="text-sm font-medium text-gray-500">Created</dt>
              <dd className="mt-1 text-sm text-gray-900">
                {new Date(claim.created_at).toLocaleString()}
              </dd>
            </div>
          </div>

          {/* Defects */}
          {claim.defect_ids && claim.defect_ids.length > 0 && (
            <div className="mt-6 pt-6 border-t border-gray-200">
              <h3 className="text-sm font-medium text-gray-900 mb-2">Defects</h3>
              <div className="space-y-2">
                {claim.defect_ids.map((defectId) => (
                  <div
                    key={defectId}
                    className="text-sm text-red-700 bg-red-50 px-3 py-2 rounded"
                  >
                    Defect ID: {defectId}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Sanad Chain */}
        <div className="bg-white shadow rounded-lg p-6">
          <h2 className="text-lg font-medium text-gray-900 mb-4">Sanad Chain (Provenance)</h2>

          {sanad ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <dt className="text-sm font-medium text-gray-500">Sanad ID</dt>
                  <dd className="mt-1 text-sm font-mono text-gray-900">{sanad.sanad_id}</dd>
                </div>
                <div>
                  <dt className="text-sm font-medium text-gray-500">Grade</dt>
                  <dd className="mt-1">
                    <GradeBadge grade={sanad.grade} />
                  </dd>
                </div>
                <div>
                  <dt className="text-sm font-medium text-gray-500">Corroboration</dt>
                  <dd className="mt-1 text-sm text-gray-900">
                    {sanad.corroboration.level} ({sanad.corroboration.independent_chain_count}{" "}
                    independent chains)
                  </dd>
                </div>
                <div>
                  <dt className="text-sm font-medium text-gray-500">Created</dt>
                  <dd className="mt-1 text-sm text-gray-900">
                    {new Date(sanad.created_at).toLocaleString()}
                  </dd>
                </div>
              </div>

              {/* Evidence Refs */}
              {sanad.evidence_refs && sanad.evidence_refs.length > 0 && (
                <div className="mt-4 pt-4 border-t border-gray-200">
                  <h3 className="text-sm font-medium text-gray-900 mb-2">Evidence References</h3>
                  <div className="bg-gray-50 p-3 rounded text-sm">
                    <pre className="whitespace-pre-wrap overflow-x-auto">
                      {JSON.stringify(sanad.evidence_refs, null, 2)}
                    </pre>
                  </div>
                </div>
              )}

              {/* Transmission Chain */}
              {sanad.transmission_chain && sanad.transmission_chain.length > 0 && (
                <div className="mt-4 pt-4 border-t border-gray-200">
                  <h3 className="text-sm font-medium text-gray-900 mb-2">Transmission Chain</h3>
                  <div className="bg-gray-50 p-3 rounded text-sm">
                    <pre className="whitespace-pre-wrap overflow-x-auto">
                      {JSON.stringify(sanad.transmission_chain, null, 2)}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <p className="text-gray-500 text-sm">No Sanad chain available for this claim.</p>
          )}
        </div>
      </main>
    </div>
  );
}
