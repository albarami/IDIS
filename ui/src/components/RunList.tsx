import Link from "next/link";
import type { RunListItem } from "@/lib/idis";

/**
 * Deal-scoped run listing (safe-shape): run id (link to detail), status, mode, timestamps.
 */
export default function RunList({ runs }: { runs: RunListItem[] }) {
  if (runs.length === 0) {
    return <p className="text-gray-500">No runs for this deal.</p>;
  }

  return (
    <div className="overflow-hidden rounded-lg bg-white shadow">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            {["Run", "Status", "Mode", "Started", "Finished"].map((heading) => (
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
          {runs.map((run) => (
            <tr key={run.run_id}>
              <td className="px-4 py-3 text-sm">
                <Link
                  href={`/runs/${run.run_id}`}
                  className="font-mono text-blue-600 hover:text-blue-800"
                >
                  {run.run_id}
                </Link>
              </td>
              <td className="px-4 py-3 text-sm text-gray-700">{run.status}</td>
              <td className="px-4 py-3 text-sm text-gray-700">{run.mode}</td>
              <td className="px-4 py-3 text-sm text-gray-500">
                {new Date(run.started_at).toLocaleString()}
              </td>
              <td className="px-4 py-3 text-sm text-gray-500">
                {run.finished_at ? new Date(run.finished_at).toLocaleString() : "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
