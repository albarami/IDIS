"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import Header from "@/components/Header";
import ErrorCallout from "@/components/ErrorCallout";
import { idis, type Deal, IDISApiError } from "@/lib/idis";

export default function RunsListPage() {
  const router = useRouter();
  const [deals, setDeals] = useState<Deal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = useState<string | undefined>(undefined);

  useEffect(() => {
    async function fetchDeals() {
      try {
        const dealsData = await idis.deals.list();
        setDeals(dealsData.items);
      } catch (err) {
        if (err instanceof IDISApiError && err.status === 401) {
          router.push("/login");
          return;
        }
        if (err instanceof IDISApiError) {
          setError(err.message);
          setErrorRequestId(err.requestId);
        } else {
          setError(err instanceof Error ? err.message : "Failed to load deals");
        }
      } finally {
        setLoading(false);
      }
    }
    fetchDeals();
  }, [router]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <main className="max-w-7xl mx-auto px-4 py-8">
          <div className="text-center text-gray-500">Loading...</div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="max-w-7xl mx-auto px-4 py-8">
        <h1 className="text-2xl font-bold text-gray-900 mb-4">Pipeline Runs</h1>
        
        <div className="mb-6 p-4 bg-blue-50 border border-blue-200 rounded-lg">
          <p className="text-sm text-blue-800">
            <strong>Note:</strong> Runs are deal-scoped. Select a deal below to view and manage its pipeline runs from the Truth Dashboard.
          </p>
        </div>

        {error && (
          <div className="mb-6">
            <ErrorCallout message={error} requestId={errorRequestId} />
          </div>
        )}

        {/* Deals List */}
        <div className="bg-white shadow rounded-lg">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-lg font-medium text-gray-900">Deals</h2>
          </div>

          {deals.length === 0 ? (
            <div className="p-8 text-center text-gray-500">
              No deals available
            </div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Deal Name
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Company
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Stage
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {deals.map((deal) => (
                  <tr key={deal.deal_id} className="hover:bg-gray-50">
                    <td className="px-6 py-4 text-sm font-medium text-gray-900">
                      {deal.name}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {deal.company_name}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {deal.stage}
                    </td>
                    <td className="px-6 py-4">
                      <Link
                        href={`/deals/${deal.deal_id}/truth-dashboard`}
                        className="text-blue-600 hover:text-blue-800 text-sm font-medium"
                      >
                        View Dashboard & Runs
                      </Link>
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
