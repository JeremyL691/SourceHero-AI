"use client";

/** Conflicts: every unresolved contradiction, newest first. */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Conflict } from "@/lib/types";
import { Card, EmptyState, ErrorNotice, Skeleton } from "@/components/ui";
import { QuoteCard } from "@/components/verdict";

export default function ConflictsPage() {
  const conflicts = useQuery({
    queryKey: ["conflicts"],
    queryFn: () => api.get<Conflict[]>("/conflicts?limit=50"),
  });

  const seen = new Set<string>();
  const rows = (conflicts.data ?? []).filter((c) => {
    if (c.cluster_id !== null && (c.a.status === "carried" || c.b.status === "carried")) {
      const docPair = [c.a.document_id, c.b.document_id].sort().join(":");
      const key = `${c.cluster_id}:${docPair}`;
      if (seen.has(key)) return false;
      seen.add(key);
    }
    return true;
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Conflicts</h1>
        <p className="text-sm text-muted">
          {rows.length} places where sources cannot both be right.
        </p>
      </header>

      <Card>
        {conflicts.isLoading ? (
          <Skeleton rows={5} />
        ) : conflicts.error ? (
          <div className="p-4">
            <ErrorNotice error={conflicts.error} onRetry={() => conflicts.refetch()} />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            title="No contradictions found"
            hint="Capture several sources on the same subject and SourceHero will start comparing them."
            action={
              <Link href="/app/inbox" className="text-sm text-accent underline underline-offset-2">
                Go to the inbox
              </Link>
            }
          />
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((conflict) => (
              <li key={conflict.id} className="space-y-2 p-4">
                <div className="grid gap-2 sm:grid-cols-2">
                  <QuoteCard
                    text={conflict.a.quote || conflict.a.statement}
                    source={conflict.a.document_title || conflict.a.subject || undefined}
                    date={conflict.a.asserted_at?.slice(0, 10)}
                    url={conflict.a.document_url || undefined}
                    onClick={() => undefined}
                  />
                  <QuoteCard
                    text={conflict.b.quote || conflict.b.statement}
                    source={conflict.b.document_title || conflict.b.subject || undefined}
                    date={conflict.b.asserted_at?.slice(0, 10)}
                    url={conflict.b.document_url || undefined}
                    onClick={() => undefined}
                  />
                </div>
                <div className="flex items-center gap-3 text-xs text-muted">
                  {conflict.cluster_id != null ? (
                    <Link
                      href={`/app/clusters/${conflict.cluster_id}`}
                      className="text-accent underline underline-offset-2"
                    >
                      Open the divergence map
                    </Link>
                  ) : (
                    <span>No cluster assigned</span>
                  )}
                  <span>confidence {conflict.confidence.toFixed(2)}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
