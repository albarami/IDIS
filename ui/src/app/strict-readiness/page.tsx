"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Header from "@/components/Header";
import ErrorCallout from "@/components/ErrorCallout";
import StrictReadinessView from "@/components/StrictReadinessView";
import { idis, type StrictReadinessReview, IDISApiError } from "@/lib/idis";

export default function StrictReadinessPage() {
  const router = useRouter();
  const [review, setReview] = useState<StrictReadinessReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = useState<string | undefined>(undefined);

  useEffect(() => {
    async function fetchReadiness() {
      try {
        setReview(await idis.readiness.get());
      } catch (err) {
        if (err instanceof IDISApiError && err.status === 401) {
          router.push("/login");
          return;
        }
        if (err instanceof IDISApiError) {
          setError(err.message);
          setErrorRequestId(err.requestId);
        } else {
          setError(err instanceof Error ? err.message : "Failed to load readiness");
        }
      } finally {
        setLoading(false);
      }
    }
    fetchReadiness();
  }, [router]);

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="max-w-7xl mx-auto px-4 py-8">
        <h1 className="mb-6 text-2xl font-bold text-gray-900">Strict full-live readiness</h1>
        {loading ? (
          <div className="text-center text-gray-500">Loading readiness...</div>
        ) : error ? (
          <ErrorCallout message={error} requestId={errorRequestId} />
        ) : review ? (
          <StrictReadinessView review={review} />
        ) : null}
      </main>
    </div>
  );
}
