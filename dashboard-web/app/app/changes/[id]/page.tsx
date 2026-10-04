"use client";

import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { BeliefDiff } from "@/lib/types";
import { Button, Card, ErrorNotice, Skeleton } from "@/components/ui";
import { MarkdownView } from "@/components/markdown";

export default function ChangeDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const diff = useQuery({
    queryKey: ["diff", id],
    queryFn: () => api.get<BeliefDiff>(`/diffs/${id}`),
  });

  if (diff.error) {
    return (
      <div className="space-y-4">
        <Button onClick={() => router.push("/app/changes")}>← Changes</Button>
        <ErrorNotice error={diff.error} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header>
        <Button variant="ghost" onClick={() => router.push("/app/changes")}>
          ← Changes
        </Button>
        <h1 className="serif mt-1 text-2xl font-semibold">
          {diff.data
            ? `${diff.data.period_start.slice(0, 10)} → ${diff.data.period_end.slice(0, 10)}`
            : "Loading…"}
        </h1>
      </header>

      <Card title="Digest">
        {diff.isLoading ? <Skeleton rows={8} /> : <div className="p-4"><MarkdownView>{diff.data?.digest_md ?? ""}</MarkdownView></div>}
      </Card>
    </div>
  );
}
