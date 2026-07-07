import type { StrictReadinessReview } from "@/lib/idis";

/**
 * Presentational view of the strict full-live readiness review.
 *
 * Safe-shape only: component modes (status enums), the blocker count + blocking component
 * names, and required env-var NAMES / service labels. It never renders env values, secrets,
 * source paths, or free-text blocker messages (the API projection already excludes them).
 */
export default function StrictReadinessView({ review }: { review: StrictReadinessReview }) {
  const blockerLabel = `${review.blocker_count} blocker${review.blocker_count === 1 ? "" : "s"}`;

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-gray-200 bg-white p-4">
        <div className="flex items-center gap-3">
          <span
            className={`inline-flex rounded-full px-3 py-1 text-sm font-semibold ${
              review.may_proceed ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"
            }`}
          >
            {review.may_proceed ? "Ready" : "Not ready"}
          </span>
          <span className="text-sm text-gray-600">{blockerLabel}</span>
        </div>
        {review.blocking_components.length > 0 && (
          <p className="mt-2 text-sm text-gray-700">
            Blocking: {review.blocking_components.join(", ")}
          </p>
        )}
      </div>

      <div className="overflow-hidden rounded-lg bg-white shadow">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              {["Component", "Mode", "May proceed", "Required env vars", "Required services"].map(
                (heading) => (
                  <th
                    key={heading}
                    className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500"
                  >
                    {heading}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 bg-white">
            {review.components.map((component) => (
              <tr key={component.component_name}>
                <td className="px-4 py-3 text-sm font-medium text-gray-900">
                  {component.component_name}
                </td>
                <td className="px-4 py-3 text-sm text-gray-700">{component.status}</td>
                <td className="px-4 py-3 text-sm">
                  <span className={component.may_proceed ? "text-green-700" : "text-red-700"}>
                    {component.may_proceed ? "yes" : "no"}
                  </span>
                </td>
                <td className="px-4 py-3 text-sm text-gray-700">
                  {component.required_env_vars.length === 0 ? (
                    <span className="text-gray-400">None</span>
                  ) : (
                    <span className="flex flex-wrap gap-1">
                      {component.required_env_vars.map((name) => (
                        <code key={name} className="rounded bg-gray-100 px-1 text-xs">
                          {name.split("=")[0]}
                        </code>
                      ))}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-sm text-gray-700">
                  {component.required_services.length === 0 ? (
                    <span className="text-gray-400">None</span>
                  ) : (
                    component.required_services.join(", ")
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
