import type { SanadGrade } from "@/lib/idis";

const gradeStyles: Record<SanadGrade, string> = {
  A: "bg-green-100 text-green-800 border-green-200",
  B: "bg-blue-100 text-blue-800 border-blue-200",
  C: "bg-yellow-100 text-yellow-800 border-yellow-200",
  D: "bg-red-100 text-red-800 border-red-200",
};

const gradeDescriptions: Record<SanadGrade, string> = {
  A: "Strong provenance (audited/verified)",
  B: "Institutional/credible but not fully audited",
  C: "Unverified founder/weak sources",
  D: "Contradicted/fabricated/broken chain",
};

interface GradeBadgeProps {
  grade: SanadGrade;
  showTooltip?: boolean;
}

export default function GradeBadge({ grade, showTooltip = true }: GradeBadgeProps) {
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${gradeStyles[grade]}`}
      title={showTooltip ? gradeDescriptions[grade] : undefined}
    >
      Grade {grade}
    </span>
  );
}
