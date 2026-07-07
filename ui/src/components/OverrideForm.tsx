"use client";

import { useState, type FormEvent } from "react";
import ErrorCallout from "@/components/ErrorCallout";
import { idis, type Override, IDISApiError } from "@/lib/idis";

const OVERRIDE_TYPES = ["IC_MEMO_CAVEAT", "DECLINE_WAIVER", "POLICY_EXCEPTION", "OTHER"];

/**
 * Override justification form over POST /v1/deals/{dealId}/overrides.
 * Renders a safe success state (override id + type) or a safe error; no private report text.
 */
export default function OverrideForm({ dealId }: { dealId: string }) {
  const [overrideType, setOverrideType] = useState("IC_MEMO_CAVEAT");
  const [justification, setJustification] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<Override | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = useState<string | undefined>(undefined);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!justification.trim()) {
      setError("A justification is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    setErrorRequestId(undefined);
    setResult(null);
    try {
      const created = await idis.overrides.create(dealId, {
        override_type: overrideType,
        justification,
      });
      setResult(created);
    } catch (err) {
      if (err instanceof IDISApiError) {
        setError(err.message);
        setErrorRequestId(err.requestId);
      } else {
        setError(err instanceof Error ? err.message : "Failed to create override");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4">
      <form
        onSubmit={handleSubmit}
        className="space-y-4 rounded-lg border border-gray-200 bg-white p-4"
      >
        <div>
          <label htmlFor="override-type" className="block text-sm font-medium text-gray-700">
            Override type
          </label>
          <select
            id="override-type"
            value={overrideType}
            onChange={(event) => setOverrideType(event.target.value)}
            className="mt-1 block w-full rounded border-gray-300 text-sm"
          >
            {OVERRIDE_TYPES.map((type) => (
              <option key={type} value={type}>
                {type.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label
            htmlFor="override-justification"
            className="block text-sm font-medium text-gray-700"
          >
            Justification
          </label>
          <textarea
            id="override-justification"
            value={justification}
            onChange={(event) => setJustification(event.target.value)}
            rows={3}
            className="mt-1 block w-full rounded border-gray-300 text-sm"
          />
        </div>
        <button
          type="submit"
          disabled={submitting}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {submitting ? "Creating..." : "Create override"}
        </button>
      </form>

      {error && <ErrorCallout message={error} requestId={errorRequestId} />}

      {result && (
        <div className="rounded-lg border border-green-200 bg-green-50 p-4">
          <h3 className="text-sm font-semibold text-green-800">Override created</h3>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <dt className="text-gray-500">Override ID</dt>
            <dd className="font-mono text-gray-900">{result.override_id}</dd>
            <dt className="text-gray-500">Type</dt>
            <dd className="text-gray-900">{result.override_type}</dd>
            <dt className="text-gray-500">Status</dt>
            <dd className="text-gray-900">{result.status}</dd>
          </dl>
        </div>
      )}
    </div>
  );
}
