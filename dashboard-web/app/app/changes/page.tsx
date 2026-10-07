"use client";

/** Belief diffs: what changed, over a period you choose. */

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Calendar } from "lucide-react";
import { api } from "@/lib/api";
import type { BeliefDiff, Claim, StaleNote } from "@/lib/types";
import { useToast } from "@/components/providers";
import { Button, Card, EmptyState, ErrorNotice, Skeleton } from "@/components/ui";
import { MarkdownView } from "@/components/markdown";

export default function ChangesPage() {
  const toast = useToast();
  const invalidate = useQueryClient();
  const [start, setStart] = useState("2026-05-01");
  const [end, setEnd] = useState("2026-09-01");
  const [axis, setAxis] = useState("world");

  // Query claims to detect available date range
  const claims = useQuery({
    queryKey: ["claims", "range"],
    queryFn: () => api.get<Claim[]>("/claims?limit=100"),
  });

  useEffect(() => {
    if (claims.data && claims.data.length > 0) {
      const dates = claims.data
        .map((c) => c.asserted_at?.slice(0, 10))
        .filter((d): d is string => Boolean(d))
        .sort();
      if (dates.length > 0) {
        const earliest = dates[0];
        const latest = dates[dates.length - 1];
        setStart(earliest);
        if (latest > earliest) {
          setEnd(latest);
        } else {
          // If all claims are on the same day, set end to next day
          const d = new Date(earliest);
          d.setDate(d.getDate() + 1);
          setEnd(d.toISOString().slice(0, 10));
        }
      }
    }
  }, [claims.data]);

  const diffs = useQuery({
    queryKey: ["diffs", 20],
    queryFn: () => api.get<BeliefDiff[]>("/diffs?limit=20"),
  });

  const staleNotes = useQuery({
    queryKey: ["stale-notes", 20],
    queryFn: () => api.get<StaleNote[]>("/stale-notes?limit=20"),
  });

  const generate = useMutation({
    mutationFn: async () => {
      const res = await api.post<BeliefDiff>("/diffs", {
        start: `${start}T00:00:00Z`,
        end: `${end}T00:00:00Z`,
        time_axis: axis,
      });

      if (res.job_id) {
        // Poll background job until completion
        let attempts = 0;
        while (attempts < 60) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          const job = await api.get<{ status: string; error_summary?: string }>(`/jobs/${res.job_id}`);
          if (job.status === "succeeded") {
            break;
          }
          if (["failed", "dead", "cancelled"].includes(job.status)) {
            throw new Error(job.error_summary || `Diff generation ${job.status}`);
          }
          attempts++;
        }
      }
      return res;
    },
    onSuccess: () => {
      toast("Diff generated");
      invalidate.invalidateQueries({ queryKey: ["diffs"] });
      invalidate.invalidateQueries({ queryKey: ["diffs", 20] });
      invalidate.invalidateQueries({ queryKey: ["diffs", 1] });
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Could not generate a diff"),
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">What changed</h1>
        <p className="text-sm text-muted">
          A digest of updated facts, new disagreements and notes that were overtaken.
        </p>
      </header>

      <Card title="Generate a diff">
        <div className="flex flex-wrap items-end gap-3 p-4">
          <label className="text-xs text-muted">
            From
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="mt-1 block rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
          </label>
          <label className="text-xs text-muted">
            To
            <input
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              className="mt-1 block rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
          </label>
          <label className="text-xs text-muted">
            Time axis
            <select
              value={axis}
              onChange={(e) => setAxis(e.target.value)}
              className="mt-1 block rounded-md border border-line bg-surface px-2 py-1 text-sm"
            >
              <option value="world">World — when it happened</option>
              <option value="observed">Observed — when you learned it</option>
            </select>
          </label>
          <Button
            variant="primary"
            onClick={() => generate.mutate()}
            disabled={generate.isPending || !start || !end}
          >
            <Calendar className="h-4 w-4" aria-hidden />
            {generate.isPending ? "Generating…" : "Generate"}
          </Button>
        </div>
      </Card>

      {/* Outdated notes section */}
      {(staleNotes.data ?? []).length > 0 && (
        <Card title={`Outdated notes (${staleNotes.data?.length ?? 0})`}>
          <ul className="divide-y divide-line">
            {(staleNotes.data ?? []).map((note) => (
              <li key={`${note.document_id}-${note.claim_id}`} className="space-y-1 p-4">
                <div className="flex items-center justify-between gap-2">
                  <Link
                    href={`/app/library/${note.document_id}`}
                    className="font-medium text-sm text-ink hover:text-accent underline underline-offset-2"
                  >
                    {note.document_title || "Untitled Note"}
                  </Link>
                  <span className="text-[11px] text-muted">
                    {note.asserted_at ? note.asserted_at.slice(0, 10) : ""}
                  </span>
                </div>
                <p className="serif text-xs text-muted">&ldquo;{note.statement}&rdquo;</p>
                <div className="flex flex-wrap gap-1 pt-0.5">
                  {note.reasons.map((r, i) => (
                    <span
                      key={i}
                      className="rounded bg-disputed-soft px-1.5 py-0.5 text-[10px] text-disputed"
                    >
                      {r.replace(/_/g, " ")}
                    </span>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {diffs.isLoading ? (
        <Skeleton rows={6} />
      ) : diffs.error ? (
        <ErrorNotice error={diffs.error} onRetry={() => diffs.refetch()} />
      ) : (diffs.data ?? []).length === 0 ? (
        <Card>
          <EmptyState
            title="No diffs yet"
            hint="Generate one above. The digest cites the claims behind every line."
          />
        </Card>
      ) : (
        (diffs.data ?? []).map((diff) => (
          <Card
            key={diff.id}
            title={`${diff.period_start.slice(0, 10)} → ${diff.period_end.slice(0, 10)}`}
            action={
              <Link
                href={`/app/changes/${diff.id}`}
                className="text-xs text-accent underline underline-offset-2"
              >
                Open
              </Link>
            }
          >
            <div className="flex flex-wrap gap-3 border-b border-line px-4 py-2 text-xs text-muted">
              <span>{diff.digest_method}</span>
              {Object.entries(diff.stats ?? {}).map(([key, value]) => (
                <span key={key}>
                  {key.replace(/_/g, " ")}: <strong className="text-ink">{value}</strong>
                </span>
              ))}
            </div>
            <div className="p-4">
              <MarkdownView>{diff.digest_md}</MarkdownView>
            </div>
          </Card>
        ))
      )}
    </div>
  );
}
