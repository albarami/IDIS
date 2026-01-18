"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter, useParams } from "next/navigation";
import Link from "next/link";
import Header from "@/components/Header";
import StatusBadge from "@/components/StatusBadge";
import ErrorCallout from "@/components/ErrorCallout";
import { idis, type Run, type DebateSession, IDISApiError } from "@/lib/idis";

export default function RunStatusPage() {
  const router = useRouter();
  const params = useParams();
  const runId = params.runId as string;

  const [run, setRun] = useState<Run | null>(null);
  const [debate, setDebate] = useState<DebateSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = useState<string | undefined>(undefined);

  const fetchData = useCallback(async () => {
    try {
      const runData = await idis.runs.get(runId);
      setRun(runData);

      // Try to fetch debate details (may not exist)
      try {
        const debateData = await idis.debate.get(runId);
        setDebate(debateData);
      } catch {
        // Debate may not exist or not be accessible
      }
    } catch (err) {
      if (err instanceof IDISApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      if (err instanceof IDISApiError) {
        setError(err.message);
        setErrorRequestId(err.requestId);
      } else {
        setError(err instanceof Error ? err.message : "Failed to load run");
      }
    } finally {
      setLoading(false);
    }
  }, [runId, router]);

  useEffect(() => {
    fetchData();

    // Poll for updates if run is still in progress
    const interval = setInterval(() => {
      if (run && (run.status === "QUEUED" || run.status === "RUNNING")) {
        fetchData();
      }
    }, 5000);

    return () => clearInterval(interval);
  }, [fetchData, run]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <main className="max-w-4xl mx-auto px-4 py-8">
          <div className="text-center text-gray-500">Loading run status...</div>
        </main>
      </div>
    );
  }

  if (error || !run) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <main className="max-w-4xl mx-auto px-4 py-8">
          <ErrorCallout message={error || "Run not found"} requestId={errorRequestId} />
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
          <span className="text-gray-600">Run</span>
        </nav>

        {/* Run Header */}
        <div className="bg-white shadow rounded-lg p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h1 className="text-xl font-bold text-gray-900">Pipeline Run</h1>
            <StatusBadge status={run.status} type="run" />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <dt className="text-sm font-medium text-gray-500">Run ID</dt>
              <dd className="mt-1 text-sm font-mono text-gray-900">{run.run_id}</dd>
            </div>
            <div>
              <dt className="text-sm font-medium text-gray-500">Started</dt>
              <dd className="mt-1 text-sm text-gray-900">
                {new Date(run.started_at).toLocaleString()}
              </dd>
            </div>
            {run.finished_at && (
              <div>
                <dt className="text-sm font-medium text-gray-500">Finished</dt>
                <dd className="mt-1 text-sm text-gray-900">
                  {new Date(run.finished_at).toLocaleString()}
                </dd>
              </div>
            )}
          </div>

          {(run.status === "QUEUED" || run.status === "RUNNING") && (
            <div className="mt-4 p-3 bg-blue-50 border border-blue-200 rounded">
              <p className="text-sm text-blue-700">
                Run is in progress. This page will automatically refresh...
              </p>
            </div>
          )}
        </div>

        {/* Debate Details */}
        {debate && (
          <div className="bg-white shadow rounded-lg p-6">
            <h2 className="text-lg font-medium text-gray-900 mb-4">Debate Session</h2>

            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <dt className="text-sm font-medium text-gray-500">Debate ID</dt>
                <dd className="mt-1 text-sm font-mono text-gray-900">{debate.debate_id}</dd>
              </div>
              <div>
                <dt className="text-sm font-medium text-gray-500">Protocol</dt>
                <dd className="mt-1 text-sm text-gray-900">{debate.protocol_version}</dd>
              </div>
              <div>
                <dt className="text-sm font-medium text-gray-500">Created</dt>
                <dd className="mt-1 text-sm text-gray-900">
                  {new Date(debate.created_at).toLocaleString()}
                </dd>
              </div>
              <div>
                <dt className="text-sm font-medium text-gray-500">Rounds</dt>
                <dd className="mt-1 text-sm text-gray-900">{debate.rounds?.length || 0}</dd>
              </div>
            </div>

            {/* Rounds */}
            {debate.rounds && debate.rounds.length > 0 && (
              <div className="mt-4 pt-4 border-t border-gray-200">
                <h3 className="text-sm font-medium text-gray-900 mb-2">Debate Rounds</h3>
                <div className="bg-gray-50 p-3 rounded max-h-96 overflow-y-auto">
                  <pre className="text-xs whitespace-pre-wrap">
                    {JSON.stringify(debate.rounds, null, 2)}
                  </pre>
                </div>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
