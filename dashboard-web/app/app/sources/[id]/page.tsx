"use client";

import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import type { Document, IngestionRun, Source } from "@/lib/types";
import { useToast } from "@/components/providers";
import { Button, Card, ErrorNotice, Skeleton, StatusDot } from "@/components/ui";

export default function SourceDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const invalidate = useQueryClient();
  const [limit, setLimit] = useState(8);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  const source = useQuery({ queryKey: ["source", id], queryFn: () => api.get<Source>(`/sources/${id}`) });
  const documents = useQuery({
    queryKey: ["source-docs", id],
    queryFn: () => api.get<Document[]>(`/documents?source_id=${id}`),
  });
  const runs = useQuery({
    queryKey: ["source-runs", id],
    queryFn: () => api.get<IngestionRun[]>(`/ingestion-runs?source_id=${id}`),
  });

  const refresh = useMutation({
    mutationFn: () => api.post(`/sources/${id}/refresh`),
    onSuccess: () => {
      toast("Queued");
      invalidate.invalidateQueries({ queryKey: ["source-runs", id] });
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed"),
  });
  const backfill = useMutation({
    mutationFn: () => {
      const params = new URLSearchParams({ limit: String(limit) });
      if (from) params.set("from", from.replaceAll("-", ""));
      if (to) params.set("to", to.replaceAll("-", ""));
      return api.post(`/sources/${id}/wayback?${params.toString()}`);
    },
    onSuccess: () => {
      toast("Backfill queued");
      invalidate.invalidateQueries({ queryKey: ["source-runs", id] });
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Backfill failed"),
  });

  if (source.error) {
    return (
      <div className="space-y-4">
        <Button onClick={() => router.back()}>Back</Button>
        <ErrorNotice error={source.error} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Button variant="ghost" onClick={() => router.push("/app/sources")}>
            ← Sources
          </Button>
          <h1 className="serif mt-1 text-2xl font-semibold break-words">
            {source.data?.name ?? "Loading…"}
          </h1>
          {source.data?.url && (
            <a
              href={source.data.url}
              className="text-sm text-accent underline underline-offset-2 break-all"
            >
              {source.data.url}
            </a>
          )}
        </div>
        <div className="flex flex-col items-end gap-2">
          {source.data && <StatusDot status={source.data.status} />}
          <Button variant="primary" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
            {refresh.isPending ? "Queuing…" : "Refresh"}
          </Button>
        </div>
      </header>

      <Card title="Documents">
        {documents.isLoading ? (
          <Skeleton />
        ) : (documents.data ?? []).length === 0 ? (
          <p className="p-4 text-sm text-muted">This source has produced no documents yet.</p>
        ) : (
          <ul className="divide-y divide-line">
            {(documents.data ?? []).map((doc) => (
              <li key={doc.id} className="px-4 py-2 text-sm">
                <span className="font-medium">{doc.title || "(untitled)"}</span>
                {doc.published_at && <span className="ml-2 text-xs text-muted">{doc.published_at.slice(0, 10)}</span>}
                {doc.url && (
                  <a
                    href={doc.url}
                    className="ml-2 text-xs text-accent underline underline-offset-2 break-all"
                  >
                    {doc.url}
                  </a>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Backfill from the Wayback Machine">
        <div className="space-y-3 p-4">
          <p className="text-sm text-muted">
            Pull archived snapshots so you can see how this page changed over time.
          </p>
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-xs text-muted">
              Snapshots
              <input
                type="number"
                min={1}
                max={50}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
                className="mt-1 block w-20 rounded-md border border-line bg-surface px-2 py-1 text-sm"
              />
            </label>
            <label className="text-xs text-muted">
              From
              <input
                type="date"
                value={from}
                onChange={(e) => setFrom(e.target.value)}
                className="mt-1 block rounded-md border border-line bg-surface px-2 py-1 text-sm"
              />
            </label>
            <label className="text-xs text-muted">
              To
              <input
                type="date"
                value={to}
                onChange={(e) => setTo(e.target.value)}
                className="mt-1 block rounded-md border border-line bg-surface px-2 py-1 text-sm"
              />
            </label>
            <Button onClick={() => backfill.mutate()} disabled={backfill.isPending}>
              {backfill.isPending ? "Queuing…" : "Backfill"}
            </Button>
          </div>
        </div>
      </Card>

      <Card title="Ingest history">
        {runs.isLoading ? (
          <Skeleton />
        ) : (runs.data ?? []).length === 0 ? (
          <p className="p-4 text-sm text-muted">This source has not been ingested yet.</p>
        ) : (
          <ul className="divide-y divide-line">
            {(runs.data ?? []).map((run) => (
              <li key={run.id} className="flex items-center justify-between gap-3 px-4 py-2 text-sm">
                <span className="min-w-0 truncate">
                  {run.started_at?.slice(0, 19).replace("T", " ")} · {run.documents_found} found
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
