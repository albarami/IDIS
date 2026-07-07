"use client";

import { useState } from "react";
import { idis, type HumanGate, IDISApiError } from "@/lib/idis";

const ACTIONS = ["APPROVE", "REJECT", "CORRECT"] as const;

function actionLabel(action: string): string {
  return action.charAt(0) + action.slice(1).toLowerCase();
}

/**
 * Approve / Reject / Correct actions for one human gate over POST /v1/deals/{dealId}/human-gates.
 * Safe-shape only: gate id, gate type, status, and the action enum. No private report text.
 */
export default function HumanGateActions({
  dealId,
  gate,
  onActionComplete,
}: {
  dealId: string;
  gate: HumanGate;
  onActionComplete: () => void;
}) {
  const [submitting, setSubmitting] = useState(false);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(action: string) {
    setSubmitting(true);
    setError(null);
    setConfirmation(null);
    try {
      await idis.humanGates.submit(dealId, { gate_id: gate.gate_id, action });
      setConfirmation(`${action} submitted`);
      onActionComplete();
    } catch (err) {
      setError(
        err instanceof IDISApiError || err instanceof Error ? err.message : "Action failed",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <span className="font-medium text-gray-900">{gate.gate_type}</span>
        <span className="text-gray-500">Status: {gate.status}</span>
        <span className="font-mono text-xs text-gray-400">{gate.gate_id}</span>
      </div>
      <div className="mt-3 flex gap-2">
        {ACTIONS.map((action) => (
          <button
            key={action}
            type="button"
            disabled={submitting}
            onClick={() => act(action)}
            className="rounded bg-gray-100 px-3 py-1 text-sm font-medium text-gray-800 hover:bg-gray-200 disabled:opacity-50"
          >
            {actionLabel(action)}
          </button>
        ))}
      </div>
      {confirmation && <p className="mt-2 text-sm text-green-700">{confirmation}</p>}
      {error && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
