"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Header from "@/components/Header";
import ErrorCallout from "@/components/ErrorCallout";
import RunList from "@/components/RunList";
import { idis, type RunListItem, IDISApiError } from "@/lib/idis";

export default function DealRunsPage() {
  const params = useParams();
  const router = useRouter();
  const dealId = params.dealId as string;
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = useState<string | undefined>(undefined);

  useEffect(() => {
    async function fetchRuns() {
      try {
        const response = await idis.runs.list(dealId, { limit: 100 });
        setRuns(response.items);
      } catch (err) {
        if (err instanceof IDISApiError && err.status === 401) {
          router.push("/login");
          return;
        }
        if (err instanceof IDISApiError) {
          setError(err.message);
          setErrorRequestId(err.requestId);
        } else {
          setError(err instanceof Error ? err.message : "Failed to load runs");
        }
      } finally {
        setLoading(false);
      }
    }
    fetchRuns();
  }, [dealId, router]);

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="mx-auto max-w-5xl px-4 py-8">
        <h1 className="mb-6 text-2xl font-bold text-gray-900">Runs</h1>
        {loading ? (
          <div className="text-center text-gray-500">Loading runs...</div>
        ) : error ? (
          <ErrorCallout message={error} requestId={errorRequestId} />
        ) : (
          <RunList runs={runs} />
        )}
      </main>
    </div>
  );
}
