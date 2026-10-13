"use client";

/** Library: every document, with its claims and current version. Filters live in the URL. */

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Suspense } from "react";
import { api } from "@/lib/api";
import { Button, Card, ClaimStatusBadge, EmptyState, ErrorNotice, Skeleton } from "@/components/ui";
import { LibraryFilters, type LibraryFilterValues } from "@/components/library/LibraryFilters";
import { ManageCollections } from "@/components/library/ManageCollections";
import { ManageTags } from "@/components/library/ManageTags";
import { useCollections, useTags } from "@/lib/queries/library";
import type { Claim, Document } from "@/lib/types";

const PAGE_SIZE = 25;
const FILTER_KEYS = ["source_type", "tag_id", "collection_id", "starred", "archived", "q"] as const;

function readFilters(params: URLSearchParams): LibraryFilterValues {
  return {
    source_type: params.get("source_type") ?? "",
    tag_id: params.get("tag_id") ?? "",
    collection_id: params.get("collection_id") ?? "",
    starred: params.get("starred") ?? "",
    archived: params.get("archived") ?? "",
    q: params.get("q") ?? "",
  };
}

function readOffset(params: URLSearchParams): number {
  const n = Number(params.get("offset"));
  return Number.isInteger(n) && n > 0 ? n : 0;
}

function filterParams(f: LibraryFilterValues): URLSearchParams {
  const p = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    if (f[key]) p.set(key, f[key]);
  }
  return p;
}

function Library() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const filters = readFilters(params);
  const offset = readOffset(params);
  const hasFilters = FILTER_KEYS.some((key) => filters[key] !== "");

  /** Every filter change goes through the URL, so a reload or a shared link keeps the view. */
  const writeUrl = (next: LibraryFilterValues, nextOffset: number) => {
    const p = filterParams(next);
    if (nextOffset > 0) p.set("offset", String(nextOffset));
    const qs = p.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  const documents = useQuery({
    queryKey: ["library-docs", filters, offset],
    queryFn: () => {
      const p = filterParams(filters);
      p.set("limit", String(PAGE_SIZE));
      p.set("offset", String(offset));
      return api.get<Document[]>(`/documents?${p.toString()}`);
    },
  });
  const claims = useQuery({
    queryKey: ["claims"],
    queryFn: () => api.get<Claim[]>("/claims?limit=200"),
  });
  const tags = useTags();
  const collections = useCollections();

  const rows = documents.data ?? [];

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Library</h1>
        <p className="text-sm text-muted">
          Newest first.
          {hasFilters && " Filtered."}
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <div className="min-w-0 space-y-4">
          <LibraryFilters
            values={filters}
            tags={tags.data ?? []}
            collections={collections.data ?? []}
            onChange={(patch) => writeUrl({ ...filters, ...patch }, 0)}
          />

          <Card>
            {documents.isLoading ? (
              <Skeleton rows={6} />
            ) : documents.error ? (
              <div className="p-4">
                <ErrorNotice error={documents.error} onRetry={() => documents.refetch()} />
              </div>
            ) : rows.length === 0 ? (
              <EmptyState
                title={hasFilters ? "Nothing matches these filters" : "No documents yet"}
                hint={
                  hasFilters
                    ? "Try different filters, or clear them."
                    : "Capture a source and it will appear here."
                }
                action={
                  hasFilters ? (
                    <Button variant="default" onClick={() => writeUrl(readFilters(new URLSearchParams()), 0)}>
                      Clear filters
                    </Button>
                  ) : (
                    <Link href="/app/inbox">
                      <Button variant="primary">Go to the inbox</Button>
                    </Link>
                  )
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
                          {doc.source_type ? ` · ${doc.source_type}` : ""}
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
                      {(doc.tags.length > 0 || doc.collections.length > 0) && (
                        <div className="mt-1.5 flex flex-wrap gap-1 text-[11px] text-muted">
                          {doc.tags.map((t) => (
                            <span key={`t${t.id}`} className="rounded-full border border-line px-1.5 py-0.5">
                              #{t.name}
                            </span>
                          ))}
                          {doc.collections.map((c) => (
                            <span key={`c${c.id}`} className="rounded-full bg-line/60 px-1.5 py-0.5">
                              {c.name}
                            </span>
                          ))}
                        </div>
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

            {!documents.error && rows.length > 0 && (
              <nav
                aria-label="Pagination"
                className="flex items-center justify-between gap-2 border-t border-line px-4 py-3 text-xs text-muted"
              >
                <span data-testid="page-range">
                  {offset + 1}–{offset + rows.length}
                </span>
                <div className="flex gap-2">
                  <Button
                    data-testid="page-prev"
                    disabled={offset === 0}
                    onClick={() => writeUrl(filters, Math.max(0, offset - PAGE_SIZE))}
                  >
                    Previous
                  </Button>
                  <Button
                    data-testid="page-next"
                    disabled={rows.length < PAGE_SIZE}
                    onClick={() => writeUrl(filters, offset + PAGE_SIZE)}
                  >
                    Next
                  </Button>
                </div>
              </nav>
            )}
          </Card>
        </div>

        <aside className="min-w-0 space-y-4">
          <ManageCollections />
          <ManageTags />
        </aside>
      </div>
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
