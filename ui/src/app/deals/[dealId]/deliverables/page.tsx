"use client";

import { useEffect, useMemo, useState, useCallback } from "react";
import { useRouter, useParams } from "next/navigation";
import Link from "next/link";
import Header from "@/components/Header";
import StatusBadge from "@/components/StatusBadge";
import ErrorCallout from "@/components/ErrorCallout";
import {
  idis,
  type Deliverable,
  type Deal,
  type ProductBundleManifestReview,
  IDISApiError,
} from "@/lib/idis";
import { generateRequestId } from "@/lib/requestId";

function getLegacyDownloadUrl(uri: string | null | undefined): string | null {
  if (!uri) return null;
  if (uri.startsWith("http://") || uri.startsWith("https://")) {
    return uri;
  }
  if (uri.startsWith("/v1/")) {
    return `/api/idis${uri}`;
  }
  return null;
}

function getDeliverableDownloadUrl(deliverable: Deliverable): string | null {
  if (deliverable.status !== "COMPLETED") {
    return null;
  }
  if (deliverable.uri?.startsWith("object:")) {
    return idis.deliverables.downloadContentUrl(deliverable.deliverable_id);
  }
  return getLegacyDownloadUrl(deliverable.uri);
}

export default function DeliverablesPage() {
  const router = useRouter();
  const params = useParams();
  const dealId = params.dealId as string;

  const [deal, setDeal] = useState<Deal | null>(null);
  const [deliverables, setDeliverables] = useState<Deliverable[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = useState<string | undefined>(undefined);
  const [generatingType, setGeneratingType] = useState<string | null>(null);
  const [manifestReview, setManifestReview] = useState<ProductBundleManifestReview | null>(null);
  const [reviewRunId, setReviewRunId] = useState<string | null>(null);
  const [reviewLoadingRunId, setReviewLoadingRunId] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [dealData, deliverablesData] = await Promise.all([
        idis.deals.get(dealId),
        idis.deliverables.list(dealId),
      ]);
      setDeal(dealData);
      setDeliverables(deliverablesData.items);
    } catch (err) {
      if (err instanceof IDISApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      if (err instanceof IDISApiError) {
        setError(err.message);
        setErrorRequestId(err.requestId);
      } else {
        setError(err instanceof Error ? err.message : "Failed to load deliverables");
      }
    } finally {
      setLoading(false);
    }
  }, [dealId, router]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const packageGroups = useMemo(() => {
    const groups = new Map<string, Deliverable[]>();
    for (const deliverable of deliverables) {
      if (deliverable.status !== "COMPLETED" || !deliverable.run_id) {
        continue;
      }
      const existing = groups.get(deliverable.run_id) ?? [];
      existing.push(deliverable);
      groups.set(deliverable.run_id, existing);
    }
    return Array.from(groups.entries()).sort((left, right) => right[0].localeCompare(left[0]));
  }, [deliverables]);

  const ungroupedDeliverables = useMemo(
    () =>
      deliverables.filter(
        (deliverable) => deliverable.status !== "COMPLETED" || !deliverable.run_id
      ),
    [deliverables]
  );

  async function handleGenerate(type: string) {
    setGeneratingType(type);
    try {
      await idis.deliverables.generate(dealId, { deliverable_type: type }, generateRequestId());
      await fetchData();
    } catch (err) {
      if (err instanceof IDISApiError) {
        setError(err.message);
        setErrorRequestId(err.requestId);
      } else {
        setError(err instanceof Error ? err.message : "Failed to generate deliverable");
      }
    } finally {
      setGeneratingType(null);
    }
  }

  async function handleReviewPackage(runId: string) {
    setReviewLoadingRunId(runId);
    try {
      const manifest = await idis.deliverables.getManifest(dealId, runId);
      setManifestReview(manifest);
      setReviewRunId(runId);
    } catch (err) {
      if (err instanceof IDISApiError) {
        setError(err.message);
        setErrorRequestId(err.requestId);
      } else {
        setError(err instanceof Error ? err.message : "Failed to load package manifest");
      }
    } finally {
      setReviewLoadingRunId(null);
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <main className="max-w-7xl mx-auto px-4 py-8">
          <div className="text-center text-gray-500">Loading deliverables...</div>
        </main>
      </div>
    );
  }

  if (error || !deal) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <main className="max-w-7xl mx-auto px-4 py-8">
          <ErrorCallout message={error || "Deal not found"} requestId={errorRequestId} />
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="max-w-7xl mx-auto px-4 py-8">
        <nav className="mb-4 text-sm">
          <Link href="/deals" className="text-blue-600 hover:text-blue-800">
            Deals
          </Link>
          <span className="mx-2 text-gray-400">/</span>
          <Link
            href={`/deals/${dealId}/truth-dashboard`}
            className="text-blue-600 hover:text-blue-800"
          >
            {deal.name}
          </Link>
          <span className="mx-2 text-gray-400">/</span>
          <span className="text-gray-600">Deliverables</span>
        </nav>

        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Deliverables</h1>
          <div className="flex gap-2">
            <button
              onClick={() => handleGenerate("screening_snapshot")}
              disabled={generatingType !== null}
              className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
            >
              {generatingType === "screening_snapshot" ? "Generating..." : "+ Screening Snapshot"}
            </button>
            <button
              onClick={() => handleGenerate("ic_memo")}
              disabled={generatingType !== null}
              className="px-4 py-2 text-sm bg-purple-600 text-white rounded hover:bg-purple-700 disabled:opacity-50"
            >
              {generatingType === "ic_memo" ? "Generating..." : "+ IC Memo"}
            </button>
          </div>
        </div>

        {packageGroups.length > 0 && (
          <section className="mb-8 space-y-4">
            <h2 className="text-lg font-semibold text-gray-900">Final Packages</h2>
            {packageGroups.map(([runId, items]) => (
              <div key={runId} className="bg-white shadow rounded-lg p-6">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <p className="text-sm text-gray-500">Run</p>
                    <code className="text-xs bg-gray-100 px-2 py-1 rounded">{runId}</code>
                  </div>
                  <button
                    onClick={() => handleReviewPackage(runId)}
                    disabled={reviewLoadingRunId === runId}
                    className="px-3 py-2 text-sm bg-gray-900 text-white rounded hover:bg-gray-800 disabled:opacity-50"
                  >
                    {reviewLoadingRunId === runId ? "Loading..." : "Review package"}
                  </button>
                </div>
                <table className="min-w-full divide-y divide-gray-200">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        Type
                      </th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        Format
                      </th>
                      <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {items.map((deliverable) => {
                      const downloadUrl = getDeliverableDownloadUrl(deliverable);
                      return (
                        <tr key={deliverable.deliverable_id}>
                          <td className="px-4 py-3 text-sm text-gray-900">
                            {deliverable.deliverable_type.replace(/_/g, " ")}
                          </td>
                          <td className="px-4 py-3 text-sm text-gray-500">
                            {deliverable.format || "—"}
                          </td>
                          <td className="px-4 py-3">
                            {downloadUrl ? (
                              <a
                                href={downloadUrl}
                                className="text-blue-600 hover:text-blue-800 text-sm font-medium"
                              >
                                Download
                              </a>
                            ) : (
                              <span className="text-gray-400 text-sm">Unavailable</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ))}
          </section>
        )}

        {manifestReview && reviewRunId && (
          <section className="mb-8 bg-white shadow rounded-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-gray-900">Package Review</h2>
              <button
                onClick={() => {
                  setManifestReview(null);
                  setReviewRunId(null);
                }}
                className="text-sm text-gray-600 hover:text-gray-800"
              >
                Close
              </button>
            </div>
            <p className="text-sm text-gray-500 mb-4">
              Run <code className="text-xs bg-gray-100 px-2 py-1 rounded">{reviewRunId}</code> ·{" "}
              {manifestReview.artifact_count} artifacts
            </p>
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                    Type
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                    Format
                  </th>
                  <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                    SHA256
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {manifestReview.artifacts.map((artifact, index) => (
                  <tr key={`${String(artifact.type)}-${index}`}>
                    <td className="px-4 py-3 text-sm text-gray-900">{String(artifact.type)}</td>
                    <td className="px-4 py-3 text-sm text-gray-500">{String(artifact.format)}</td>
                    <td className="px-4 py-3 text-sm text-gray-500 font-mono">
                      {typeof artifact.sha256 === "string"
                        ? `${artifact.sha256.slice(0, 16)}...`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        <div className="bg-white shadow rounded-lg">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">All Deliverables</h2>
          </div>
          {deliverables.length === 0 ? (
            <div className="p-8 text-center text-gray-500">
              <p className="mb-2">No deliverables generated yet</p>
              <p className="text-sm">Click the buttons above to generate deliverables</p>
            </div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Type
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Created
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    URI
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {(ungroupedDeliverables.length > 0 ? ungroupedDeliverables : deliverables).map(
                  (deliverable) => {
                    const downloadUrl = getDeliverableDownloadUrl(deliverable);
                    return (
                      <tr key={deliverable.deliverable_id} className="hover:bg-gray-50">
                        <td className="px-6 py-4 text-sm font-medium text-gray-900">
                          {deliverable.deliverable_type.replace(/_/g, " ").toUpperCase()}
                        </td>
                        <td className="px-6 py-4">
                          <StatusBadge status={deliverable.status} type="run" />
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500">
                          {new Date(deliverable.created_at).toLocaleString()}
                        </td>
                        <td className="px-6 py-4 text-sm text-gray-500">
                          {deliverable.uri ? (
                            <code className="text-xs bg-gray-100 px-2 py-1 rounded">
                              {deliverable.uri.length > 40
                                ? `${deliverable.uri.substring(0, 40)}...`
                                : deliverable.uri}
                            </code>
                          ) : (
                            <span className="text-gray-400">No URI</span>
                          )}
                        </td>
                        <td className="px-6 py-4">
                          {downloadUrl ? (
                            <a
                              href={downloadUrl}
                              className="text-blue-600 hover:text-blue-800 text-sm font-medium"
                            >
                              Download
                            </a>
                          ) : deliverable.uri ? (
                            <button
                              onClick={() => navigator.clipboard.writeText(deliverable.uri!)}
                              className="text-gray-600 hover:text-gray-800 text-sm"
                            >
                              Copy URI
                            </button>
                          ) : (
                            <span className="text-gray-400 text-sm">N/A</span>
                          )}
                        </td>
                      </tr>
                    );
                  }
                )}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  );
}
