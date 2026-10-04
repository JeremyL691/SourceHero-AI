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

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Conflicts</h1>
        <p className="text-sm text-muted">
          {conflicts.data?.length ?? 0} places where sources cannot both be right.
        </p>
      </header>

      <Card>
        {conflicts.isLoading ? (
          <Skeleton rows={5} />
        ) : conflicts.error ? (
          <div className="p-4">
            <ErrorNotice error={conflicts.error} onRetry={() => conflicts.refetch()} />
          </div>
        ) : (conflicts.data ?? []).length === 0 ? (
          <EmptyState
            title="No contradictions found"
            hint="Capture several sources on the same subject and SourceHero will start comparing them."
            action={
              <Link href="/app/inbox">
                <a className="text-sm text-accent underline underline-offset-2">Go to the inbox</a>
              </Link>
            }
          />
        ) : (
          <ul className="divide-y divide-line">
            {(conflicts.data ?? []).map((conflict) => (
              <li key={conflict.id} className="space-y-2 p-4">
                <div className="grid gap-2 sm:grid-cols-2">
                  <QuoteCard
                    text={conflict.a.quote || conflict.a.statement}
                    source={conflict.a.subject || undefined}
                    date={conflict.a.asserted_at?.slice(0, 10)}
                    url={conflict.a.statement ? undefined : null}
                    onClick={() => undefined}
                  />
                  <QuoteCard
                    text={conflict.b.quote || conflict.b.statement}
                    source={conflict.b.subject || undefined}
                    date={conflict.b.asserted_at?.slice(0, 10)}
                    onClick={() => undefined}
                  />
                </div>
                <div className="flex items-center gap-3 text-xs text-muted">
                  <Link
                    href={`/app/clusters/${conflict.cluster_id}`}
                    className="text-accent underline underline-offset-2"
                  >
                    Open the divergence map
                  </Link>
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
