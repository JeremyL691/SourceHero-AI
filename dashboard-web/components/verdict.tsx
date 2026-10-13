"use client";

/** Evidence-shaped components: citations, highlighted spans, verdicts, diffs. */

import clsx from "clsx";
import { useState } from "react";
import { VerdictBadge } from "./ui";
import type { Citation, Verdict } from "@/lib/types";

export function CitationChip({
  citation,
  onOpen,
}: {
  citation: Citation;
  onOpen?: (c: Citation) => void;
}) {
  return (
    <button
      onClick={() => onOpen?.(citation)}
      title={`${citation.title}${citation.date ? ` · ${citation.date}` : ""}\n\n"${citation.quote}"`}
      data-citation={citation.n}
      className="mx-0.5 inline-flex max-w-[22rem] items-center gap-1 rounded border border-line bg-paper px-1.5 py-0.5 align-baseline text-[11px] text-muted hover:border-accent hover:text-accent"
    >
      <span className="mono shrink-0 text-accent">[{citation.n}]</span>
      <span className="truncate">{citation.source || citation.title}</span>
    </button>
  );
}

export function CitationList({
  citations,
  onOpen,
}: {
  citations: Citation[];
  onOpen?: (c: Citation) => void;
}) {
  if (!citations.length) return null;
  return (
    <ul className="space-y-2">
      {citations.map((c) => (
        <li key={`${c.n}-${c.chunk_id}`}>
          <button
            onClick={() => onOpen?.(c)}
            className="w-full rounded-md border border-line bg-paper p-2 text-left hover:border-accent"
          >
            <div className="flex items-baseline gap-2 text-xs">
              <span className="mono text-accent">[{c.n}]</span>
              <span className="font-medium text-ink">{c.source || c.title}</span>
              {c.date && <span className="text-muted">{c.date}</span>}
            </div>
            <p className="serif mt-1 text-xs leading-snug text-muted">&ldquo;{c.quote}&rdquo;</p>
          </button>
        </li>
      ))}
    </ul>
  );
}

export function QuoteCard({
  text,
  source,
  date,
  url,
  verdict,
  onClick,
}: {
  text: string;
  source?: string;
  date?: string | null;
  url?: string | null;
  verdict?: Verdict;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "block w-full rounded-md border border-line bg-surface p-3 text-left",
        onClick && "hover:border-accent",
      )}
    >
      <p className="serif text-sm leading-snug text-ink">&ldquo;{text}&rdquo;</p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted">
        {source && <span>{source}</span>}
        {date && <span>· {date}</span>}
        {url && (
          <span className="truncate underline underline-offset-2">
            {url.replace(/^https?:\/\//, "")}
          </span>
        )}
        {verdict && <VerdictBadge verdict={verdict} small />}
      </div>
    </button>
  );
}

/** Render text with one or more [start, end) ranges highlighted.
 *
 * Offsets address the document's stored `clean_text`, so a highlight can never drift
 * from the sentence it points at. */
export function SpanText({
  text,
  ranges,
  onSelect,
  className,
}: {
  text: string;
  ranges: { start: number; end: number; id?: number; kind?: string }[];
  onSelect?: (id?: number) => void;
  className?: string;
}) {
  const [hovered, setHovered] = useState<number | null>(null);

  const sorted = [...ranges]
    .filter((r) => r.start >= 0 && r.end <= text.length && r.end > r.start)
    .sort((a, b) => a.start - b.start);

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  sorted.forEach((range, index) => {
    // Overlapping ranges would corrupt the walk; the first one wins.
    if (range.start < cursor) return;
    if (range.start > cursor) parts.push(text.slice(cursor, range.start));
    parts.push(
      <mark
        key={index}
        data-range-id={range.id}
        data-kind={range.kind}
        role="button"
        tabIndex={0}
        onMouseEnter={() => setHovered(index)}
        onMouseLeave={() => setHovered(null)}
        onClick={() => onSelect?.(range.id)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect?.(range.id);
          }
        }}
        className={clsx(
          "highlight cursor-pointer rounded-sm px-0.5",
          hovered === index && "outline outline-1 outline-accent",
        )}
      >
        {text.slice(range.start, range.end)}
      </mark>,
    );
    cursor = range.end;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));

  return <span className={clsx("serif text-sm leading-relaxed", className)}>{parts}</span>;
}

export function CampColumns({
  camps,
  balanced,
}: {
  camps: { label: string; claims: { claim_id: number; statement: string; quote: string; date: string | null }[] }[];
  balanced: boolean;
}) {
  if (camps.length < 2) {
    return <p className="p-4 text-sm text-muted">No disagreement recorded here yet.</p>;
  }
  return (
    <div>
      {!balanced && (
        <p className="mx-4 mt-3 rounded border border-disputed/40 bg-disputed-soft px-3 py-2 text-xs text-disputed">
          These sources cannot be split cleanly into two sides: some pair contradicts
          more than one other claim. Treat any grouping below as provisional.
        </p>
      )}
      <div className="grid gap-4 p-4 md:grid-cols-2">
        {camps.map((camp) => (
          <div key={camp.label}>
            <h3 className="serif mb-2 text-sm font-semibold text-ink">Camp {camp.label}</h3>
            <div className="space-y-2">
              {camp.claims.map((claim) => (
                <QuoteCard
                  key={claim.claim_id}
                  text={claim.quote || claim.statement}
                  date={claim.date}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function DiffView({ diff }: { diff: string }) {
  const lines = diff ? diff.split("\n") : [];
  if (!lines.length) {
    return <p className="p-4 text-sm text-muted">These versions are identical.</p>;
  }
  return (
    <pre className="mono overflow-x-auto p-3 text-xs leading-relaxed">
      {lines.map((line, i) => (
        <div
          key={i}
          className={clsx(
            line.startsWith("+") && "bg-supported-soft text-supported",
            line.startsWith("-") && "bg-contradicted-soft text-contradicted",
            line.startsWith("@@") && "text-accent",
          )}
        >
          {line}
        </div>
      ))}
    </pre>
  );
}

export function TimelineList({
  entries,
  onSelect,
}: {
  entries: {
    at: string;
    label: string;
    event: string;
    claim_id: number;
    statement: string;
    document_title: string;
  }[];
  onSelect?: (claimId: number) => void;
}) {
  if (!entries.length) {
    return <p className="p-4 text-sm text-muted">Nothing has happened yet.</p>;
  }
  return (
    <ol className="space-y-0 p-4">
      {entries.map((entry, i) => (
        <li key={i} className="relative flex gap-3 pb-4 pl-1">
          <span
            aria-hidden
            className={clsx(
              "mt-1.5 h-2 w-2 shrink-0 rounded-full",
              entry.event === "superseded" && "bg-superseded",
              entry.event === "retracted" && "bg-unsupported",
              entry.event === "conflict_opened" && "bg-contradicted",
              entry.event === "conflict_resolved" && "bg-supported",
              entry.event === "asserted" && "bg-accent",
            )}
          />
          <div className="min-w-0">
            <p className="text-[11px] text-muted">
              <time dateTime={entry.at}>{entry.at.slice(0, 10)}</time> · {entry.label}
              {entry.document_title && <> · {entry.document_title}</>}
            </p>
            <button
              onClick={() => onSelect?.(entry.claim_id)}
              className="serif block text-left text-sm text-ink hover:text-accent"
            >
              {entry.statement}
            </button>
          </div>
        </li>
      ))}
    </ol>
  );
}
