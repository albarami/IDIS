import type { ClaimVerdict } from "@/lib/idis";

const verdictStyles: Record<ClaimVerdict, string> = {
  VERIFIED: "bg-green-100 text-green-800",
  CONTRADICTED: "bg-red-100 text-red-800",
  UNVERIFIED: "bg-gray-100 text-gray-800",
  SUBJECTIVE: "bg-purple-100 text-purple-800",
  INFLATED: "bg-orange-100 text-orange-800",
};

interface VerdictBadgeProps {
  verdict: ClaimVerdict;
}

export default function VerdictBadge({ verdict }: VerdictBadgeProps) {
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${verdictStyles[verdict]}`}
    >
      {verdict}
    </span>
  );
}
