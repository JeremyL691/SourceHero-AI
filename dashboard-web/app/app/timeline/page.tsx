"use client";

import { useQuery } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { api } from "@/lib/api";
import type { Cluster, Document, TimelineEntry, Topic } from "@/lib/types";
import { Button, Card, ErrorNotice, Skeleton, Tabs } from "@/components/ui";
import { TimelineList } from "@/components/verdict";

export default function TimelinePage() {
  return (
    <Suspense fallback={<Skeleton rows={8} />}>
      <TimelineView />
    </Suspense>
  );
}

const FILTER_KEYS = ["cluster_id", "topic_id", "document_id", "from", "to"] as const;
type FilterKey = (typeof FILTER_KEYS)[number];

function TimelineView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  // Every control lives in the URL, so a reload or a shared link keeps the same view.
  const axis = params.get("axis") === "observed" ? "observed" : "world";
  const filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? ""])) as Record<
    FilterKey,
    string
  >;
  const hasFilters = FILTER_KEYS.some((k) => filters[k] !== "");

  const update = (changes: Record<string, string>) => {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  const query = new URLSearchParams({ axis, limit: "100" });
  if (filters.cluster_id) query.set("cluster_id", filters.cluster_id);
  if (filters.topic_id) query.set("topic_id", filters.topic_id);
  if (filters.document_id) query.set("document_id", filters.document_id);
  // `from` / `to` are sent as full timestamps so the whole chosen days are included.
  if (filters.from) query.set("from", `${filters.from}T00:00:00Z`);
  if (filters.to) query.set("to", `${filters.to}T23:59:59Z`);

  const timeline = useQuery({
    queryKey: ["timeline", query.toString()],
    queryFn: () => api.get<TimelineEntry[]>(`/timeline?${query.toString()}`),
    // The timeline moves as ingestion runs; a slow poll keeps it honest without noise.
    refetchInterval: 15_000,
  });

  const clusters = useQuery({
    queryKey: ["timeline-filter", "clusters"],
    queryFn: () => api.get<Cluster[]>("/clusters?limit=100"),
  });
  const topics = useQuery({
    queryKey: ["timeline-filter", "topics"],
    queryFn: () => api.get<Topic[]>("/topics?limit=100"),
  });
  const documents = useQuery({
    queryKey: ["timeline-filter", "documents"],
    queryFn: () => api.get<Document[]>("/documents?limit=100"),
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Timeline</h1>
        <p className="text-sm text-muted">
          Every claim event, in order — by when it happened, or by when you learned it.
        </p>
      </header>

      <Tabs
        active={axis}
        onChange={(id) => update({ axis: id === "world" ? "" : id })}
        tabs={[
          { id: "world", label: "World time" },
          { id: "observed", label: "Observed" },
        ]}
      />

      <Card title="Filters">
        <div className="flex flex-wrap items-end gap-3 p-4">
          <SelectFilter
            label="Cluster"
            value={filters.cluster_id}
            onChange={(v) => update({ cluster_id: v })}
            options={(clusters.data ?? []).map((c) => ({ value: String(c.id), label: c.label || `Cluster ${c.id}` }))}
          />
          <SelectFilter
            label="Topic"
            value={filters.topic_id}
            onChange={(v) => update({ topic_id: v })}
            options={(topics.data ?? []).map((t) => ({ value: String(t.id), label: t.label || t.slug }))}
          />
          <SelectFilter
            label="Document"
            value={filters.document_id}
            onChange={(v) => update({ document_id: v })}
            options={(documents.data ?? []).map((d) => ({ value: d.id, label: d.title || "(untitled)" }))}
          />
          <label className="text-xs text-muted">
            From
            <input
              type="date"
              value={filters.from}
              onChange={(e) => update({ from: e.target.value })}
              className="mt-1 block rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
          </label>
          <label className="text-xs text-muted">
            To
            <input
              type="date"
              value={filters.to}
              onChange={(e) => update({ to: e.target.value })}
              className="mt-1 block rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
          </label>
          {hasFilters && (
            <Button
              variant="ghost"
              onClick={() => update(Object.fromEntries(FILTER_KEYS.map((k) => [k, ""])))}
            >
              Clear filters
            </Button>
          )}
        </div>
      </Card>

      <Card>
        {timeline.isLoading ? (
          <Skeleton rows={8} />
        ) : timeline.error ? (
          <div className="p-4">
            <ErrorNotice error={timeline.error} onRetry={() => timeline.refetch()} />
          </div>
        ) : (
          <TimelineList entries={timeline.data ?? []} />
        )}
      </Card>
    </div>
  );
}

function SelectFilter({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="text-xs text-muted">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 block max-w-xs rounded-md border border-line bg-surface px-2 py-1 text-sm"
      >
        <option value="">All</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
