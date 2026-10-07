"use client";

/** Library: every document, with its claims and current version. */

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { api } from "@/lib/api";
import { Button, Card, ClaimStatusBadge, EmptyState, ErrorNotice, Skeleton } from "@/components/ui";
import type { Claim, Document } from "@/lib/types";

function Library() {
  const params = useSearchParams();
  const documents = useQuery({
    queryKey: ["documents"],
    queryFn: () => api.get<Document[]>("/documents?limit=100"),
  });
  const claims = useQuery({
    queryKey: ["claims"],
    queryFn: () => api.get<Claim[]>("/claims?limit=200"),
  });

  const needle = (params.get("q") ?? "").toLowerCase();
  const rows = (documents.data ?? []).filter(
    (d) =>
      !needle ||
      d.title.toLowerCase().includes(needle) ||
      (d.url ?? "").toLowerCase().includes(needle),
  );

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Library</h1>
        <p className="text-sm text-muted">
          {documents.data?.length ?? 0} documents, newest first.
          {needle && ` filtered by “${needle}”`}
        </p>
      </header>

      <Card>
        {documents.isLoading ? (
          <Skeleton rows={6} />
        ) : documents.error ? (
          <div className="p-4">
            <ErrorNotice error={documents.error} onRetry={() => documents.refetch()} />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            title={needle ? "Nothing matches that search" : "No documents yet"}
            hint={
              needle
                ? "Try a different term, or clear the search box."
                : "Capture a source and it will appear here."
            }
            action={
              <Link href="/app/inbox">
                <Button variant="primary">Go to the inbox</Button>
              </Link>
            }
          />
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((doc) => {
              const docClaims = (claims.data ?? []).filter((c) => c.document_id === doc.id);
              return (
                <li key={doc.id} className="px-4 py-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <Link
                      href={`/app/library/${doc.id}`}
                      className="serif text-sm font-medium hover:text-accent"
                    >
                      {doc.title || "(untitled)"}
                    </Link>
                    <span className="text-xs text-muted">
                      {doc.published_at ? doc.published_at.slice(0, 10) : "undated"}
                      {doc.lang ? ` · ${doc.lang}` : ""}
                    </span>
                  </div>
                  {doc.url && (
                    <a
                      href={doc.url}
                      className="mt-0.5 block truncate text-xs text-muted underline underline-offset-2"
                    >
                      {doc.url}
                    </a>
                  )}
                  {docClaims.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {docClaims.slice(0, 3).map((claim) => (
                        <ClaimStatusBadge key={claim.id} status={claim.status} small />
                      ))}
                      {docClaims.length > 3 && (
                        <span className="text-[11px] text-muted">
                          +{docClaims.length - 3} more
                        </span>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}

export default function LibraryPage() {
  return (
    <Suspense fallback={<p className="p-4 text-sm text-muted">Loading…</p>}>
      <Library />
    </Suspense>
  );
}
