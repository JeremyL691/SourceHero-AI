"use client";

/** Inbox: capture material, and load the demo corpus. */

import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import type { IngestionRun, Source } from "@/lib/types";
import { useToast } from "@/components/providers";
import { Button, Card, EmptyState, ErrorNotice, Skeleton, StatusDot } from "@/components/ui";

export default function Inbox() {
  const router = useRouter();
  const toast = useToast();
  const invalidate = useQueryClient();
  const [value, setValue] = useState("");
  const [file, setFile] = useState<File | null>(null);

  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.get<IngestionRun[]>("/ingestion-runs?limit=10"),
  });
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.get<Source[]>("/sources") });

  const capture = useMutation({
    mutationFn: async () => {
      const body = /^(https?:\/\/)/i.test(value.trim())
        ? { url: value.trim() }
        : { text: value.trim(), title: value.trim().slice(0, 80) };
      return api.post("/captures", body);
    },
    onSuccess: () => {
      setValue("");
      toast("Captured");
      invalidate.invalidateQueries();
      router.refresh();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Capture failed"),
  });

  const upload = useMutation({
    mutationFn: async () => {
      const body = new FormData();
      body.append("file", file!);
      return api.form("/sources/upload", body);
    },
    onSuccess: () => {
      setFile(null);
      toast("Uploaded — ingesting in the background");
      invalidate.invalidateQueries();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Upload failed"),
  });

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
        <div className="space-y-3 p-4">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="https://example.com/article — or just type a note"
              aria-label="URL or note"
              className="flex-1 rounded-md border border-line bg-surface px-3 py-2 text-sm"
            />
            <Button
              variant="primary"
              onClick={() => capture.mutate()}
              disabled={capture.isPending || !value.trim()}
            >
              {capture.isPending ? "Capturing…" : "Capture"}
            </Button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <input
              type="file"
              accept=".pdf,.md,.txt,.html,.htm"
              aria-label="Upload a file"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-sm"
            />
            <Button onClick={() => upload.mutate()} disabled={upload.isPending || !file}>
              {upload.isPending ? "Uploading…" : "Upload"}
            </Button>
            <span className="text-xs text-muted">pdf, md, txt or html — up to 25 MB</span>
          </div>
        </div>
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
