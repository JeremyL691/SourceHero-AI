"use client";

/** Belief diffs: what changed, over a period you choose. */

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Calendar } from "lucide-react";
import { api } from "@/lib/api";
import type { BeliefDiff } from "@/lib/types";
import { useToast } from "@/components/providers";
import { Button, Card, EmptyState, ErrorNotice, Skeleton } from "@/components/ui";
import { MarkdownView } from "@/components/markdown";

export default function ChangesPage() {
  const toast = useToast();
  const invalidate = useQueryClient();
  const [start, setStart] = useState("2026-05-01");
  const [end, setEnd] = useState("2026-09-01");
  const [axis, setAxis] = useState("world");

  const diffs = useQuery({ queryKey: ["diffs"], queryFn: () => api.get<BeliefDiff[]>("/diffs?limit=20") });

  const generate = useMutation({
    mutationFn: () =>
      api.post<BeliefDiff>("/diffs", {
        start: `${start}T00:00:00Z`,
        end: `${end}T00:00:00Z`,
        time_axis: axis,
      }),
    onSuccess: () => {
      toast("Diff generated");
      invalidate.invalidateQueries();
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
