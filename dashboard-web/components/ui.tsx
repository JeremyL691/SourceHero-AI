"use client";

/** The small set of primitives every page is built from. */

import clsx from "clsx";
import { AlertTriangle, Check, Copy, Info, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Verdict } from "@/lib/types";

export function Button({
  variant = "default",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost" | "danger";
}) {
  const styles = {
    default:
      "border border-line bg-surface text-ink hover:border-accent hover:text-accent",
    primary: "border border-accent bg-accent text-white hover:opacity-90",
    ghost: "border border-transparent bg-transparent text-muted hover:text-ink",
    danger: "border border-contradicted text-contradicted hover:bg-contradicted-soft",
  }[variant];
  return (
    <button
      {...props}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium",
        "transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        styles,
        className,
      )}
    />
  );
}

export function Card({
  title,
  action,
  className,
  children,
}: {
  title?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={clsx("rounded-lg border border-line bg-surface", className)}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold tracking-wide text-ink uppercase">{title}</h2>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

const VERDICT_STYLES: Record<string, { bg: string; fg: string; label: string; icon: string }> = {
  supported: {
    bg: "bg-supported-soft",
    fg: "text-supported",
    label: "Supported",
    icon: "✓",
  },
  contradicted: {
    bg: "bg-contradicted-soft",
    fg: "text-contradicted",
    label: "Contradicted",
    icon: "✗",
  },
  disputed: {
    bg: "bg-disputed-soft",
    fg: "text-disputed",
    label: "Disputed",
    icon: "≠",
  },
  unsupported: {
    bg: "bg-unsupported-soft",
    fg: "text-unsupported",
    label: "Unsupported",
    icon: "?",
  },
  superseded: {
    bg: "bg-superseded-soft",
    fg: "text-superseded",
    label: "Superseded",
    icon: "↻",
  },
};

/** Colour is never the only signal: each verdict also carries a glyph and a word. */
export function VerdictBadge({ verdict, small }: { verdict: Verdict | string; small?: boolean }) {
  const style = VERDICT_STYLES[verdict] ?? VERDICT_STYLES.unsupported;
  return (
    <span
      data-verdict={verdict}
      title={style.label}
      className={clsx(
        "inline-flex items-center gap-1 rounded-full font-medium",
        small ? "px-1.5 py-0.5 text-[11px]" : "px-2 py-0.5 text-xs",
        style.bg,
        style.fg,
      )}
    >
      <span aria-hidden>{style.icon}</span>
      {style.label}
    </span>
  );
}

const CLAIM_STATUS_STYLES: Record<string, { bg: string; fg: string; label: string; icon: string }> = {
  active: {
    bg: "bg-supported-soft",
    fg: "text-supported",
    label: "Active",
    icon: "●",
  },
  carried: {
    bg: "bg-paper",
    fg: "text-muted",
    label: "Carried",
    icon: "→",
  },
  superseded: {
    bg: "bg-superseded-soft",
    fg: "text-superseded",
    label: "Superseded",
    icon: "↻",
  },
  retracted: {
    bg: "bg-contradicted-soft",
    fg: "text-contradicted",
    label: "Retracted",
    icon: "⊘",
  },
};

export function ClaimStatusBadge({
  status,
  small,
}: {
  status: string;
  small?: boolean;
}) {
  const style = CLAIM_STATUS_STYLES[status] ?? {
    bg: "bg-paper",
    fg: "text-muted",
    label: status,
    icon: "·",
  };
  return (
    <span
      data-status={status}
      title={style.label}
      className={clsx(
        "inline-flex items-center gap-1 rounded-full font-medium",
        small ? "px-1.5 py-0.5 text-[11px]" : "px-2 py-0.5 text-xs",
        style.bg,
        style.fg,
      )}
    >
      <span aria-hidden>{style.icon}</span>
      {style.label}
    </span>
  );
}

const STATUS_STYLES: Record<string, string> = {
  clean: "bg-supported-soft text-supported",
  active: "bg-supported-soft text-supported",
  queued: "bg-disputed-soft text-disputed",
  running: "bg-accent-soft text-accent",
  compiling: "bg-accent-soft text-accent",
  dirty: "bg-disputed-soft text-disputed",
  failed: "bg-contradicted-soft text-contradicted",
  dead: "bg-contradicted-soft text-contradicted",
  succeeded: "bg-supported-soft text-supported",
  cancelled: "bg-unsupported-soft text-unsupported",
  paused: "bg-unsupported-soft text-unsupported",
  superseded: "bg-superseded-soft text-superseded",
  retracted: "bg-unsupported-soft text-unsupported",
  carried: "bg-unsupported-soft text-unsupported",
};

export function StatusDot({ status }: { status: string }) {
  return (
    <span
      data-status={status}
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
        STATUS_STYLES[status] ?? "bg-unsupported-soft text-unsupported",
      )}
    >
      <span aria-hidden className="inline-block h-1.5 w-1.5 rounded-full bg-current" />
      {status}
    </span>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-14 text-center">
      <Info className="h-5 w-5 text-muted" aria-hidden />
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="max-w-md text-sm text-muted">{hint}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-4" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-4 animate-pulse rounded bg-line/70" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-md border border-contradicted/40 bg-contradicted-soft px-3 py-2 text-sm text-contradicted"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button onClick={onRetry} className="font-medium underline underline-offset-2">
          Retry
        </button>
      )}
    </div>
  );
}

export function CodeBlock({ children }: { children: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(children);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="relative">
      <pre className="mono overflow-x-auto rounded-md border border-line bg-paper p-3 text-xs">
        {children}
      </pre>
      <button
        onClick={copy}
        aria-label={copied ? "Copied" : "Copy to clipboard"}
        className="absolute top-2 right-2 rounded border border-line bg-surface p-1 text-muted hover:text-accent"
      >
        {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
      </button>
    </div>
  );
}

export function Badge({ children, tone = "default" }: { children: React.ReactNode; tone?: string }) {
  return (
    <span
      className={clsx(
        "rounded-full px-2 py-0.5 text-[11px] font-medium",
        tone === "default" ? "bg-line/60 text-muted" : tone,
      )}
    >
      {children}
    </span>
  );
}

export function Tabs({
  tabs,
  active,
  onChange,
}: {
  tabs: { id: string; label: string }[];
  active: string;
  onChange: (id: string) => void;
}) {
  return (
    <div role="tablist" className="flex gap-1 border-b border-line">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={active === tab.id}
          onClick={() => onChange(tab.id)}
          className={clsx(
            "-mb-px border-b-2 px-3 py-2 text-sm",
            active === tab.id
              ? "border-accent font-medium text-accent"
              : "border-transparent text-muted hover:text-ink",
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function Dialog({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    if (!open && node.open) node.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={title}
      className="m-auto rounded-lg border border-line bg-surface p-0 text-ink backdrop:bg-black/40"
    >
      <header className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        <button onClick={onClose} aria-label="Close" className="text-muted hover:text-ink">
          <X className="h-4 w-4" aria-hidden />
        </button>
      </header>
      <div className="p-4">{children}</div>
    </dialog>
  );
}

export const VERDICT_LEGEND = VERDICT_STYLES;
