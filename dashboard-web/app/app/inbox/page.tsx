"use client";

/** Inbox: capture material, and load the demo corpus. */

import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { IngestionRun, Source } from "@/lib/types";
import { useToast } from "@/components/providers";
import { Button, Card, EmptyState, ErrorNotice, Skeleton, StatusDot } from "@/components/ui";
import { CapturePanel } from "@/components/inbox/CapturePanel";
import { ImportCard } from "@/components/inbox/ImportCard";

export default function Inbox() {
  const router = useRouter();
  const toast = useToast();
  const invalidate = useQueryClient();

  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.get<IngestionRun[]>("/ingestion-runs?limit=10"),
  });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.get<Source[]>("/sources") });

  const seed = useMutation({
    mutationFn: () => api.post("/demo/seed", {}),
    onSuccess: () => {
      toast("Demo corpus queued");
      invalidate.invalidateQueries();
      router.refresh();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Could not queue the demo corpus"),
  });

  const empty = (sources.data?.length ?? 0) === 0;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Inbox</h1>
        <p className="text-sm text-muted">
          Paste a link or a note, or drop a file. Everything lands in your library.
        </p>
      </header>

      {empty && (
        <Card>
          <EmptyState
            title="Nothing here yet"
            hint="Load the demo corpus to see how SourceHero reads a set of sources that disagree with each other."
            action={
              <Button
                variant="primary"
                onClick={() => seed.mutate()}
                disabled={seed.isPending}
              >
                {seed.isPending ? "Queuing…" : "Load demo corpus"}
              </Button>
            }
          />
        </Card>
      )}

      <Card title="Capture">
        <CapturePanel />
      </Card>

      <Card title="Import">
        <ImportCard />
      </Card>

      <Card title="Recent activity">
        {runs.isLoading ? (
          <Skeleton />
        ) : runs.error ? (
          <div className="p-4">
            <ErrorNotice error={runs.error} onRetry={() => runs.refetch()} />
          </div>
        ) : (runs.data ?? []).length === 0 ? (
          <EmptyState title="No ingests yet" hint="Captured sources appear here as they are read." />
        ) : (
          <ul className="divide-y divide-line">
            {(runs.data ?? []).map((run) => (
              <li key={run.id} className="flex items-center justify-between gap-3 px-4 py-2 text-sm">
                <span className="min-w-0 truncate">
                  {run.documents_found} found · {run.documents_new} new ·{" "}
                  {run.documents_changed} changed
                  {run.error && <span className="text-contradicted"> · {run.error}</span>}
                </span>
                <StatusDot status={run.status} />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
