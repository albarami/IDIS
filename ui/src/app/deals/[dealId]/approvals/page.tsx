"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Header from "@/components/Header";
import ErrorCallout from "@/components/ErrorCallout";
import HumanGateActions from "@/components/HumanGateActions";
import OverrideForm from "@/components/OverrideForm";
import { idis, type HumanGate, IDISApiError } from "@/lib/idis";

export default function ApprovalsPage() {
  const params = useParams();
  const router = useRouter();
  const dealId = params.dealId as string;
  const [gates, setGates] = useState<HumanGate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = useState<string | undefined>(undefined);

  const fetchGates = useCallback(async () => {
    try {
      const response = await idis.humanGates.list(dealId, { limit: 100 });
      setGates(response.items);
    } catch (err) {
      if (err instanceof IDISApiError && err.status === 401) {
        router.push("/login");
        return;
      }
      if (err instanceof IDISApiError) {
        setError(err.message);
        setErrorRequestId(err.requestId);
      } else {
        setError(err instanceof Error ? err.message : "Failed to load human gates");
      }
    } finally {
      setLoading(false);
    }
  }, [dealId, router]);

  useEffect(() => {
    fetchGates();
  }, [fetchGates]);

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="mx-auto max-w-3xl space-y-8 px-4 py-8">
        <section>
          <h1 className="mb-4 text-2xl font-bold text-gray-900">Human approvals</h1>
          {loading ? (
            <div className="text-center text-gray-500">Loading human gates...</div>
          ) : error ? (
            <ErrorCallout message={error} requestId={errorRequestId} />
          ) : gates.length === 0 ? (
            <p className="text-gray-500">No human gates for this deal.</p>
          ) : (
            <div className="space-y-3">
              {gates.map((gate) => (
                <HumanGateActions
                  key={gate.gate_id}
                  dealId={dealId}
                  gate={gate}
                  onActionComplete={fetchGates}
                />
              ))}
            </div>
          )}
        </section>

        <section>
          <h2 className="mb-4 text-xl font-semibold text-gray-900">Register an override</h2>
          <OverrideForm dealId={dealId} />
        </section>
      </main>
    </div>
  );
}
