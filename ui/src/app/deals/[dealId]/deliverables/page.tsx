"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter, useParams } from "next/navigation";
import Link from "next/link";
import Header from "@/components/Header";
import StatusBadge from "@/components/StatusBadge";
import ErrorCallout from "@/components/ErrorCallout";
import { idis, type Deliverable, type Deal, IDISApiError } from "@/lib/idis";
import { generateRequestId } from "@/lib/requestId";

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

  function getDownloadUrl(uri: string | null | undefined): string | null {
    if (!uri) return null;

    // HTTP(S) URLs can be opened directly
    if (uri.startsWith("http://") || uri.startsWith("https://")) {
      return uri;
    }

    // API paths should go through the proxy
    if (uri.startsWith("/v1/")) {
      return `/api/idis${uri}`;
    }

    // Other URIs are not downloadable
    return null;
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
        {/* Breadcrumb */}
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

        {/* Header */}
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

        {/* Deliverables List */}
        <div className="bg-white shadow rounded-lg">
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
                {deliverables.map((deliverable) => {
                  const downloadUrl = getDownloadUrl(deliverable.uri);
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
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600 hover:text-blue-800 text-sm font-medium"
                          >
                            Open
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
                })}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  );
}
