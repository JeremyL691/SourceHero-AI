"use client";

/** The divergence map: two camps, and a way to correct the machine. */

import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ClaimRelation, Cluster, TimelineEntry } from "@/lib/types";
import { useToast } from "@/components/providers";
import { Button, Card, ErrorNotice, Skeleton } from "@/components/ui";
import { CampColumns, TimelineList } from "@/components/verdict";

const LABELS = [
  ["confirmed", "Confirm"],
  ["rejected", "Not a conflict"],
  ["contradicts", "Contradicts"],
  ["supports", "Supports"],
] as const;

export default function ClusterPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const invalidate = useQueryClient();

  const cluster = useQuery({
    queryKey: ["cluster", id],
    queryFn: () => api.get<Cluster>(`/clusters/${id}`),
  });
  const timeline = useQuery({
    queryKey: ["cluster-timeline", id],
    queryFn: () => api.get<TimelineEntry[]>(`/timeline?cluster_id=${id}&limit=40`),
  });

  const label = useMutation({
    mutationFn: ({ relationId, userLabel }: { relationId: number; userLabel: string }) =>
      api.patch<ClaimRelation>(`/relations/${relationId}`, { user_label: userLabel }),
    onSuccess: () => {
      toast("Saved — your label overrides future runs");
      invalidate.invalidateQueries();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Could not save the label"),
  });

  if (cluster.error) {
    return (
      <div className="space-y-4">
        <Button onClick={() => router.push("/app/conflicts")}>← Conflicts</Button>
        <ErrorNotice error={cluster.error} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header>
        <Button variant="ghost" onClick={() => router.push("/app/conflicts")}>
          ← Conflicts
        </Button>
        <h1 className="serif mt-1 text-2xl font-semibold">
          {cluster.data?.label || "Untitled cluster"}
        </h1>
        {cluster.data && (
          <p className="text-sm text-muted">
            {cluster.data.size} claims from {cluster.data.doc_count} sources ·{" "}
            {cluster.data.conflict_count} contradictions
          </p>
        )}
      </header>

      <Card title="Divergence map">
        {cluster.isLoading ? (
          <Skeleton rows={6} />
        ) : cluster.data?.camps ? (
          <CampColumns camps={cluster.data.camps.camps} balanced={cluster.data.camps.balanced} />
        ) : (
          <p className="p-4 text-sm text-muted">
            This cluster has claims but no recorded disagreement yet.
          </p>
        )}
      </Card>

      <Card title="Timeline">
        {timeline.isLoading ? (
          <Skeleton rows={5} />
        ) : (
          <TimelineList entries={timeline.data ?? []} />
        )}
      </Card>

      <Card title="Correct the machine">
        <div className="space-y-2 p-4 text-sm">
          <p className="text-muted">
            SourceHero read two claims as a contradiction. If that is wrong, say so and it
            will not be repeated.
          </p>
          <RelationLabels clusterId={id!} onLabel={(relationId, value) => label.mutate({ relationId, userLabel: value })} />
        </div>
      </Card>
    </div>
  );
}

function RelationLabels({
  clusterId,
  onLabel,
}: {
  clusterId: string;
  onLabel: (relationId: number, value: string) => void;
}) {
  const conflicts = useQuery({
    queryKey: ["conflicts"],
    queryFn: () => api.get<{ id: number }[]>("/conflicts?limit=50"),
  });
  return (
    <ul className="space-y-2">
      {(conflicts.data ?? []).slice(0, 10).map((conflict) => (
        <li key={conflict.id} className="flex flex-wrap items-center gap-2">
          <span className="mono text-[11px] text-muted">#{conflict.id}</span>
          {LABELS.map(([value, text]) => (
            <button
              key={value}
              onClick={() => onLabel(conflict.id, value)}
              className="rounded border border-line px-2 py-0.5 text-xs text-muted hover:border-accent hover:text-accent"
            >
              {text}
            </button>
          ))}
        </li>
      ))}
      <li className="sr-only">{clusterId}</li>
    </ul>
  );
}
