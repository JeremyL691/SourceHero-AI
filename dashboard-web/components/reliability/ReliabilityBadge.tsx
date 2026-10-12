"use client";

/** A small text badge for a source's reliability; renders nothing when the level is not known to the page. */

export type ReliabilityLevel = "unknown" | "low" | "medium" | "high";

const TONES: Record<ReliabilityLevel, string> = {
  high: "bg-emerald-100 text-emerald-800",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-rose-100 text-rose-800",
  unknown: "bg-line/60 text-muted",
};

export function ReliabilityBadge({ level }: { level?: string | null }) {
  if (!level) return null;
  const key: ReliabilityLevel = level in TONES ? (level as ReliabilityLevel) : "unknown";
  return (
    <span
      data-testid="k8-reliability-badge"
      data-level={key}
      className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-medium ${TONES[key]}`}
    >
      Reliability: {key}
    </span>
  );
}
