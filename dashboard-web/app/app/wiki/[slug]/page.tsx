"use client";

/** One compiled wiki page: the prose, its footnotes, and how to rebuild it. */

import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { Citation, Claim, WikiPage } from "@/lib/types";
import { useToast } from "@/components/providers";
import { Button, Card, ErrorNotice, Skeleton, StatusDot } from "@/components/ui";
import { MarkdownView } from "@/components/markdown";

export default function WikiDetail() {
  const { slug } = useParams<{ slug: string }>();
  const router = useRouter();
  const toast = useToast();

  const page = useQuery({
    queryKey: ["wiki-page", slug],
    queryFn: () => api.get<WikiPage>(`/wiki/pages/${slug}`),
  });

  // Footnotes are keyed by the number printed in the page. Each resolves to the claim
  // that was actually cited, fetched from the ledger -- never a placeholder, because a
  // footnote the reader cannot follow is worse than none.
  const claims = useQuery({
    queryKey: ["claims"],
    queryFn: () => api.get<Claim[]>("/claims?limit=500"),
  });

  const footnotes = useMemo<Record<number, Citation>>(() => {
    const byId = new Map((claims.data ?? []).map((c) => [c.id, c]));
    const map: Record<number, Citation> = {};
    (page.data?.claim_ids ?? []).forEach((claimId, index) => {
      const claim = byId.get(claimId);
      map[index + 1] = {
        n: index + 1,
        chunk_id: claim?.chunk_id ?? 0,
        document_id: claim?.document_id ?? "",
        version_id: claim?.version_id ?? 0,
        title: claim ? claim.quote || claim.statement : `Source claim ${claimId}`,
        url: null,
        source: claim ? `claim #${claim.id}` : "",
        date: claim ? claim.asserted_at.slice(0, 10) : null,
        quote: claim?.quote ?? "",
        char_start: claim?.char_start ?? 0,
        char_end: claim?.char_end ?? 0,
        page: null,
      };
    });
    return map;
  }, [claims.data, page.data?.claim_ids]);

  const rebuild = useMutation({
    mutationFn: () => api.post<WikiPage>(`/wiki/pages/${slug}/compile`),
    onSuccess: () => {
      toast("Recompiled");
      page.refetch();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Recompile failed"),
  });

  if (page.error) {
    return (
      <div className="space-y-4">
        <Button onClick={() => router.push("/app/wiki")}>← Wiki</Button>
        <ErrorNotice error={page.error} />
      </div>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <div className="space-y-4">
        <header>
          <Button variant="ghost" onClick={() => router.push("/app/wiki")}>
            ← Wiki
          </Button>
          <h1 className="serif mt-1 text-2xl font-semibold">{page.data?.title ?? "Loading…"}</h1>
          {page.data && (
            <div className="mt-1 flex items-center gap-2 text-xs text-muted">
              <StatusDot status={page.data.status} />
              <span>revision {page.data.revision}</span>
              <span>{page.data.sentence_count} sentences</span>
              {page.data.compiled_at && <span>· compiled {page.data.compiled_at.slice(0, 10)}</span>}
            </div>
          )}
        </header>

        {page.data?.error && (
          <p role="alert" className="rounded-md border border-contradicted/40 bg-contradicted-soft px-3 py-2 text-sm text-contradicted">
            {page.data.error}
          </p>
        )}

        <Card>
          {page.isLoading ? (
            <Skeleton rows={10} />
          ) : (
            <div className="p-5">
              <MarkdownView footnotes={footnotes}>{page.data?.body_md ?? ""}</MarkdownView>
            </div>
          )}
        </Card>
      </div>

      <div className="space-y-4">
        <Card title="Provenance">
          <div className="space-y-2 p-4 text-sm">
            <p className="text-muted">
              This page was compiled from {page.data?.claim_ids?.length ?? 0} claims. Every
              sentence above is a source statement with a citation.
            </p>
            <ul className="mono space-y-0.5 text-xs text-muted">
              {(page.data?.claim_ids ?? []).slice(0, 20).map((claimId) => (
                <li key={claimId}>claim #{claimId}</li>
              ))}
            </ul>
          </div>
        </Card>

        <Card title="Rebuild">
          <div className="space-y-2 p-4 text-sm">
            <p className="text-muted">
              A page rebuilds when its claims change. You can also force a full
              recompile.
            </p>
            <Button onClick={() => rebuild.mutate()} disabled={rebuild.isPending}>
              <RefreshCw className="h-4 w-4" aria-hidden />
              {rebuild.isPending ? "Recompiling…" : "Recompile now"}
            </Button>
          </div>
        </Card>

        <Card title="Backlinks">
          {(page.data?.backlinks ?? []).length === 0 ? (
            <p className="p-4 text-sm text-muted">Nothing links here yet.</p>
          ) : (
            <ul className="divide-y divide-line">
              {(page.data?.backlinks ?? []).map((slug) => (
                <li key={slug} className="px-4 py-2">
                  <button
                    onClick={() => router.push(`/app/wiki/${slug}`)}
                    className="serif text-sm hover:text-accent"
                  >
                    {slug}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
