"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Source } from "@/lib/types";
import { useToast } from "@/components/providers";
import { Button, Card, EmptyState, ErrorNotice, Skeleton, StatusDot } from "@/components/ui";

export default function SourcesPage() {
  const toast = useToast();
  const invalidate = useQueryClient();
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api.get<Source[]>("/sources") });

  const refresh = useMutation({
    mutationFn: (id: string) => api.post(`/sources/${id}/refresh`),
    onSuccess: () => {
      toast("Queued for re-ingest");
      invalidate.invalidateQueries();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Refresh failed"),
  });

  const watch = useMutation({
    mutationFn: ({ id, value }: { id: string; value: boolean }) =>
      api.patch(`/sources/${id}`, { watch: value }),
    onSuccess: () => invalidate.invalidateQueries(),
    onError: (e) => toast(e instanceof Error ? e.message : "Could not update the watch"),
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Sources</h1>
        <p className="text-sm text-muted">
          {sources.data?.length ?? 0} sources in this library.
        </p>
      </header>

      <Card>
        {sources.isLoading ? (
          <Skeleton rows={5} />
        ) : sources.error ? (
          <div className="p-4">
            <ErrorNotice error={sources.error} onRetry={() => sources.refetch()} />
          </div>
        ) : (sources.data ?? []).length === 0 ? (
          <EmptyState
            title="No sources yet"
            hint="Capture a URL from the inbox, or load the demo corpus."
            action={
              <Link href="/app/inbox">
                <Button variant="primary">Go to the inbox</Button>
              </Link>
            }
          />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs tracking-wide text-muted uppercase">
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Type</th>
                <th className="px-4 py-2 font-medium">Documents</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Last read</th>
                <th className="px-4 py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {(sources.data ?? []).map((source) => (
                <tr key={source.id}>
                  <td className="max-w-xs truncate px-4 py-2">
                    <Link href={`/app/sources/${source.id}`} className="hover:text-accent">
                      {source.name}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-muted">{source.source_type}</td>
                  <td className="px-4 py-2 text-muted">{source.document_count}</td>
                  <td className="px-4 py-2">
                    <StatusDot status={source.status} />
                  </td>
                  <td className="px-4 py-2 text-xs text-muted">
                    {source.last_ingested_at ? source.last_ingested_at.slice(0, 10) : "never"}
                  </td>
                  <td className="px-4 py-2">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => refresh.mutate(source.id)}
                        className="text-xs text-accent underline underline-offset-2"
                      >
                        Refresh
                      </button>
                      <label className="flex items-center gap-1 text-xs text-muted">
                        <input
                          type="checkbox"
                          checked={source.watch}
                          onChange={(e) => watch.mutate({ id: source.id, value: e.target.checked })}
                        />
                        Watch
                      </label>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
