import type { DealStatus, RunStatus } from "@/lib/idis";

const dealStatusStyles: Record<DealStatus, string> = {
  NEW: "bg-gray-100 text-gray-800",
  INTAKE: "bg-blue-100 text-blue-800",
  SCREENING: "bg-yellow-100 text-yellow-800",
  DEEP_DIVE: "bg-purple-100 text-purple-800",
  IC_READY: "bg-green-100 text-green-800",
  DECLINED: "bg-red-100 text-red-800",
  ARCHIVED: "bg-gray-100 text-gray-600",
};

const runStatusStyles: Record<RunStatus, string> = {
  QUEUED: "bg-gray-100 text-gray-800",
  RUNNING: "bg-blue-100 text-blue-800",
  SUCCEEDED: "bg-green-100 text-green-800",
  FAILED: "bg-red-100 text-red-800",
};

interface StatusBadgeProps {
  status: DealStatus | RunStatus | string;
  type?: "deal" | "run";
}

export default function StatusBadge({ status, type = "deal" }: StatusBadgeProps) {
  const styles =
    type === "run"
      ? runStatusStyles[status as RunStatus] || "bg-gray-100 text-gray-800"
      : dealStatusStyles[status as DealStatus] || "bg-gray-100 text-gray-800";

  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${styles}`}
    >
      {status.replace(/_/g, " ")}
    </span>
  );
}
