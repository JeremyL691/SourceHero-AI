"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import type { TimelineEntry } from "@/lib/types";
import { Card, ErrorNotice, Skeleton, Tabs } from "@/components/ui";
import { TimelineList } from "@/components/verdict";

export default function TimelinePage() {
  const [axis, setAxis] = useState("world");
  const timeline = useQuery({
    queryKey: ["timeline", axis],
    queryFn: () => api.get<TimelineEntry[]>(`/timeline?axis=${axis}&limit=100`),
    // The timeline moves as ingestion runs; a slow poll keeps it honest without noise.
    refetchInterval: 15_000,
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
        onChange={setAxis}
        tabs={[
          { id: "world", label: "World time" },
          { id: "observed", label: "Observed" },
        ]}
      />

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
