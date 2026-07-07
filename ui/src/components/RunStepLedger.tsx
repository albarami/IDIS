import type { RunStep } from "@/lib/idis";

/**
 * Run-monitor step ledger + blocker detail (safe-shape): step names, statuses, retry counts,
 * and the stable error/blocker CODES. The free-text error message is not rendered.
 */
export default function RunStepLedger({
  steps,
  blockReason,
}: {
  steps: RunStep[];
  blockReason?: string | null;
}) {
  return (
    <div className="space-y-4">
      {blockReason && (
        <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          Blocked: {blockReason}
        </div>
      )}
      {steps.length > 0 && (
        <div className="overflow-hidden rounded-lg bg-white shadow">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                {["Step", "Status", "Error", "Retries"].map((heading) => (
                  <th
                    key={heading}
                    className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500"
                  >
                    {heading}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {steps.map((step) => (
                <tr key={step.step_name}>
                  <td className="px-4 py-3 text-sm font-medium text-gray-900">{step.step_name}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{step.status}</td>
                  <td className="px-4 py-3 text-sm">
                    {step.error ? (
                      <span className="font-mono text-xs text-red-700">{step.error.code}</span>
                    ) : (
                      <span className="text-gray-400">-</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-500">{step.retry_count ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
