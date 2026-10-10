"use client";

/** Overview: what the corpus holds, and what to look at next. */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { BeliefDiff, Cluster, Document, Job, Source, StaleNote } from "@/lib/types";
import { Button, Card, EmptyState, ErrorNotice, Skeleton, StatusDot } from "@/components/ui";

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div
      className="rounded-lg border border-line bg-surface px-4 py-3"
      data-testid={`stat-${label.toLowerCase().replace(/\s+/g, "-")}`}
    >
      <p className="text-2xl font-semibold text-ink">{value}</p>
      <p className="text-xs tracking-wide text-muted uppercase">{label}</p>
    </div>
  );
}

export default function Overview() {
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.get<Source[]>("/sources") });
  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => api.get<Document[]>("/documents?limit=100"),
  });
  const conflicts = useQuery({ queryKey: ["clusters"], queryFn: () => api.get<Cluster[]>("/clusters?conflicted=true") });
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.get<Job[]>("/jobs?limit=10"),
    // Poll only while something is actually in flight; a static queue needs no timer.
    refetchInterval: (query) => {
      const rows = query.state.data as Job[] | undefined;
      const busy = rows?.some((j) => j.status === "queued" || j.status === "running");
      return busy ? 2000 : false;
    },
  });
  const diffs = useQuery({ queryKey: ["diffs", 1], queryFn: () => api.get<BeliefDiff[]>("/diffs?limit=1") });
  const staleNotes = useQuery({
    queryKey: ["stale-notes", 5],
    queryFn: () => api.get<StaleNote[]>("/stale-notes?limit=5"),
  });

  const loading = sources.isLoading || documents.isLoading;
  const error = sources.error || documents.error;
  const latest = diffs.data?.[0];
  const busyJobs = (jobs.data ?? []).filter((j) => j.status === "queued" || j.status === "running");

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="serif text-2xl font-semibold">Overview</h1>
          <p className="text-sm text-muted">What your sources say, and where they disagree.</p>
        </div>
      </header>

      {error && <ErrorNotice error={error} onRetry={() => sources.refetch()} />}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {loading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-[86px] animate-pulse rounded-lg border border-line bg-surface" />
          ))
        ) : (
          <>
            <Stat label="Sources" value={sources.data?.length ?? 0} />
            <Stat label="Documents" value={documents.data?.length ?? 0} />
            <Stat label="Disagreements" value={conflicts.data?.length ?? 0} />
            <Stat
              label="Claims superseded"
              value={latest?.stats?.superseded ?? 0}
            />
          </>
        )}
      </div>

      {busyJobs.length > 0 && (
        <Card title="Working">
          <ul className="divide-y divide-line">
            {busyJobs.map((job) => (
              <li key={job.id} className="flex items-center justify-between gap-3 px-4 py-2 text-sm">
                <span className="truncate">{job.kind}</span>
                <StatusDot status={job.status} />
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card
          title="Most contested"
          action={
            <Link href="/app/conflicts" className="text-xs text-accent underline underline-offset-2">
              All conflicts
            </Link>
          }
        >
          {conflicts.isLoading ? (
            <Skeleton />
          ) : (conflicts.data ?? []).length === 0 ? (
            <EmptyState
              title="No disagreements yet"
              hint="Capture a few sources and SourceHero will start comparing them."
              action={
                <Link href="/app/inbox">
                  <Button variant="primary">Load the demo corpus</Button>
                </Link>
              }
            />
          ) : (
            <ul className="divide-y divide-line">
              {(conflicts.data ?? []).slice(0, 5).map((cluster) => (
                <li key={cluster.id} className="px-4 py-2">
                  <Link
                    href={`/app/clusters/${cluster.id}`}
                    className="flex items-center justify-between gap-3 hover:text-accent"
                  >
                    <span className="truncate text-sm">{cluster.label || "Untitled cluster"}</span>
                    <span className="shrink-0 text-xs text-muted">
                      {cluster.conflict_count} conflicts
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="What changed"
          action={
            <Link href="/app/changes" className="text-xs text-accent underline underline-offset-2">
              All changes
            </Link>
          }
        >
          {!latest ? (
            <EmptyState
              title="No belief diff yet"
              hint="Generate one to see what your sources have updated."
            />
          ) : (
            <div className="p-4">
              <p className="mb-2 text-xs text-muted">
                {latest.period_start.slice(0, 10)} → {latest.period_end.slice(0, 10)} ·{" "}
                {latest.digest_method}
              </p>
              <pre className="serif max-h-64 overflow-y-auto text-sm whitespace-pre-wrap text-ink">
                {latest.digest_md}
              </pre>
            </div>
          )}
        </Card>
      </div>

      {(staleNotes.data ?? []).length > 0 && (
        <Card
          title={`Outdated notes (${staleNotes.data?.length ?? 0})`}
          action={
            <Link href="/app/changes" className="text-xs text-accent underline underline-offset-2">
              All changes
            </Link>
          }
        >
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
    </div>
  );
}
