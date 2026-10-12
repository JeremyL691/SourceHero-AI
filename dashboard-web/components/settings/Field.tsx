"use client";

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-xs text-muted">
      {label}
      {hint && <span className="block text-[11px] opacity-70">{hint}</span>}
      <span className="mt-1 block">{children}</span>
    </label>
  );
}
