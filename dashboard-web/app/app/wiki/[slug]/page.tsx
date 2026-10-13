"use client";

/** One compiled wiki page: the prose, its footnotes, and how to rebuild it. */

import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { WikiFootnote, WikiPage } from "@/lib/types";
import { useToast } from "@/components/providers";
import { Button, Card, ErrorNotice, Skeleton, StatusDot } from "@/components/ui";
import { MarkdownView } from "@/components/markdown";
import { DiffView } from "@/components/verdict";
import { ShareButton } from "@/components/sharing/ShareButton";

/** GET /wiki/pages/{slug}/revisions item (newest first). compile_method is not in RevisionOut today; shown only if sent. */
type WikiRevision = {
  revision: number;
  change_summary: string;
  created_at?: string | null;
  compile_method?: string;
};

/** GET /wiki/pages/{slug}/revisions/{from}/diff?to={to} */
type RevisionDiff = {
  from: number;
  to: number;
  diff: string;
  sections: { added: string[]; removed: string[]; changed: string[] };
};

/** Fetch a Markdown export with the bearer token and save it as a file. */
async function downloadBrief(path: string, filename: string) {
  const blob = await api.blob(path);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export default function WikiDetail() {
  const { slug } = useParams<{ slug: string }>();
  const router = useRouter();
  const toast = useToast();

  const page = useQuery({
    queryKey: ["wiki-page", slug],
    queryFn: () => api.get<WikiPage>(`/wiki/pages/${slug}`),
  });

  const footnotes = useMemo<Record<number, WikiFootnote>>(() => {
    const map: Record<number, WikiFootnote> = {};
    (page.data?.footnotes ?? []).forEach((fn) => {
      map[fn.n] = fn;
    });
    return map;
  }, [page.data?.footnotes]);

  const revisions = useQuery({
    queryKey: ["wiki-revisions", slug],
    queryFn: () => api.get<WikiRevision[]>(`/wiki/pages/${slug}/revisions`),
  });

  const [selected, setSelected] = useState<number | null>(null);
  const selectedIndex = (revisions.data ?? []).findIndex((r) => r.revision === selected);
  // The list is newest first, so the previous revision is the next entry.
  const previous = selectedIndex >= 0 ? revisions.data?.[selectedIndex + 1] : undefined;

  const revisionDiff = useQuery({
    queryKey: ["wiki-revision-diff", slug, previous?.revision, selected],
    queryFn: () =>
      api.get<RevisionDiff>(
        `/wiki/pages/${slug}/revisions/${previous?.revision}/diff?to=${selected}`,
      ),
    enabled: previous !== undefined && selected !== null,
  });

  const rebuild = useMutation({
    mutationFn: () => api.post<WikiPage>(`/wiki/pages/${slug}/compile`),
    onSuccess: () => {
      toast("Recompiled");
      page.refetch();
      revisions.refetch();
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
          {page.data && (
            <Button
              variant="default"
              className="mt-2 text-xs"
              onClick={() =>
                downloadBrief(
                  `/topics/${page.data.topic_id}/brief?format=md`,
                  `topic-${page.data.topic_id}.md`,
                ).catch((e) => toast(e instanceof Error ? e.message : "Export failed"))
              }
            >
              Export brief
            </Button>
          )}
          <ShareButton kind="wiki_page" targetId={slug} />
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

        <Card title="Revision history">
          {revisions.error ? (
            <div className="p-4">
              <ErrorNotice error={revisions.error} />
            </div>
          ) : revisions.isLoading ? (
            <div className="p-4">
              <Skeleton rows={3} />
            </div>
          ) : (revisions.data ?? []).length === 0 ? (
            <p className="p-4 text-sm text-muted" data-testid="revisions-empty">
              No earlier revisions yet.
            </p>
          ) : (
            <div className="space-y-3 p-4 text-sm">
              <ul className="divide-y divide-line" data-testid="revision-list">
                {(revisions.data ?? []).map((rev) => (
                  <li key={rev.revision}>
                    <button
                      type="button"
                      aria-pressed={selected === rev.revision}
                      data-testid={`revision-item-${rev.revision}`}
                      onClick={() => setSelected(rev.revision)}
                      className={`block w-full rounded px-2 py-2 text-left hover:bg-accent-soft ${
                        selected === rev.revision ? "bg-accent-soft text-accent" : ""
                      }`}
                    >
                      <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
                        <span className="mono font-semibold text-ink">r{rev.revision}</span>
                        {rev.created_at && (
                          <span>{rev.created_at.slice(0, 16).replace("T", " ")}</span>
                        )}
                        {rev.compile_method && <span>· {rev.compile_method}</span>}
                      </div>
                      <p className="mt-0.5 text-sm text-ink">
                        {rev.change_summary || "No summary recorded."}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>

              {selected === null ? (
                <p className="text-xs text-muted">Select a revision to see what changed.</p>
              ) : previous === undefined ? (
                <p className="text-xs text-muted" data-testid="revision-first">
                  r{selected} is the first revision, so there is nothing earlier to compare.
                </p>
              ) : revisionDiff.error ? (
                <ErrorNotice error={revisionDiff.error} />
              ) : revisionDiff.isLoading || !revisionDiff.data ? (
                <Skeleton rows={4} />
              ) : (
                <div className="space-y-2" data-testid="revision-diff">
                  <p className="text-xs text-muted">
                    r{previous.revision} → r{selected}
                  </p>
                  <ul className="mono space-y-0.5 text-xs" data-testid="revision-sections">
                    <li>
                      <span className="text-supported">added</span>:{" "}
                      {revisionDiff.data.sections.added.join(", ") || "none"}
                    </li>
                    <li>
                      <span className="text-contradicted">removed</span>:{" "}
                      {revisionDiff.data.sections.removed.join(", ") || "none"}
                    </li>
                    <li>
                      <span className="text-accent">changed</span>:{" "}
                      {revisionDiff.data.sections.changed.join(", ") || "none"}
                    </li>
                  </ul>
                  <div className="overflow-hidden rounded border border-line">
                    <DiffView diff={revisionDiff.data.diff} />
                  </div>
                </div>
              )}
            </div>
          )}
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
