"use client";

/** The reader: a document's current text with every claim highlighted in place. */

import { useParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Claim, Document, Version } from "@/lib/types";
import { Button, Card, ErrorNotice, Skeleton, VerdictBadge } from "@/components/ui";
import { DiffView, SpanText } from "@/components/verdict";

export default function Reader() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [versionId, setVersionId] = useState<number | null>(null);
  const [compare, setCompare] = useState(false);
  const [selected, setSelected] = useState<number | undefined>();

  const doc = useQuery({ queryKey: ["doc", id], queryFn: () => api.get<Document>(`/documents/${id}`) });
  const versions = useQuery({
    queryKey: ["versions", id],
    queryFn: () => api.get<Version[]>(`/documents/${id}/versions`),
  });
  const claims = useQuery({
    queryKey: ["doc-claims", id, versionId],
    queryFn: () =>
      api.get<Claim[]>(
        `/documents/${id}/claims${versionId ? `?version_id=${versionId}` : ""}`,
      ),
  });

  const active = useMemo(() => {
    if (versionId != null) return versions.data?.find((v) => v.id === versionId) ?? null;
    const current = versions.data?.find((v) => v.id === doc.data?.current_version_id);
    return current ?? null;
  }, [versionId, versions.data, doc.data?.current_version_id]);

  const text = active?.clean_text ?? doc.data?.text ?? "";
  const visible = (claims.data ?? []).filter((c) => c.status !== "carried");
  const ranges = visible.map((c) => ({
    start: c.char_start,
    end: c.char_end,
    id: c.id,
    kind: c.status,
  }));

  if (doc.error) {
    return (
      <div className="space-y-4">
        <Button onClick={() => router.push("/app/library")}>← Library</Button>
        <ErrorNotice error={doc.error} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header>
        <Button variant="ghost" onClick={() => router.push("/app/library")}>
          ← Library
        </Button>
        <h1 className="serif mt-1 text-2xl font-semibold break-words">
          {doc.data?.title || "(untitled)"}
        </h1>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
          <label>
            Version
            <select
              value={versionId ?? doc.data?.current_version_id ?? ""}
              onChange={(e) => {
                const raw = e.target.value;
                setVersionId(raw === "" ? null : Number(raw));
                setCompare(false);
              }}
              className="ml-1 rounded border border-line bg-surface px-1.5 py-0.5"
            >
              {(versions.data ?? []).map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version_no} · {v.effective_at.slice(0, 10)} · {v.origin}
                </option>
              ))}
            </select>
          </label>
          {(versions.data ?? []).length > 1 && (
            <button
              onClick={() => setCompare((c) => !c)}
              className="text-accent underline underline-offset-2"
            >
              {compare ? "Hide diff" : "Compare versions"}
            </button>
          )}
        </div>
      </header>

      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <Card title="Text">
          {doc.isLoading || (!active && versions.isLoading) ? (
            <Skeleton rows={8} />
          ) : (
            <div className="p-4">
              <SpanText text={text} ranges={ranges} onSelect={setSelected} />
            </div>
          )}
        </Card>

        <Card title={`Claims (${visible.length})`}>
          {claims.isLoading ? (
            <Skeleton />
          ) : visible.length === 0 ? (
            <p className="p-4 text-sm text-muted">
              No claims extracted from this document yet.
            </p>
          ) : (
            <ul className="max-h-[32rem] divide-y divide-line overflow-y-auto">
              {visible.map((claim) => (
                <li key={claim.id}>
                  <button
                    onClick={() => setSelected(claim.id)}
                    className={`block w-full px-4 py-2 text-left hover:bg-paper ${
                      selected === claim.id ? "bg-accent-soft" : ""
                    }`}
                  >
                    <div className="mb-1 flex items-center gap-2">
                      <VerdictBadge verdict={claim.status} small />
                      <span className="text-[11px] text-muted">
                        {claim.asserted_at.slice(0, 10)}
                      </span>
                    </div>
                    <p className="serif text-xs leading-snug text-ink">{claim.statement}</p>
                    <p className="mt-1 text-[11px] text-muted">
                      chars {claim.char_start}–{claim.char_end}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {compare && (versions.data ?? []).length > 1 && (
        <Card title="Version diff">
          <DiffTable slug={id!} versions={versions.data ?? []} currentId={versionId ?? doc.data?.current_version_id ?? 0} />
        </Card>
      )}
    </div>
  );
}

function DiffTable({
  slug,
  versions,
  currentId,
}: {
  slug: string;
  versions: Version[];
  currentId: number;
}) {
  const [target, setTarget] = useState(versions.length > 1 ? versions[versions.length - 2].id : currentId);
  const diff = useQuery({
    queryKey: ["diff", slug, target, currentId],
    queryFn: () =>
      api.get<{ diff: string }>(`/documents/${slug}/diff?from=${target}&to=${currentId}`),
    enabled: versions.length > 1 && target !== currentId,
  });
  return (
    <div>
      <label className="block px-4 pt-3 text-xs text-muted">
        Compare against
        <select
          value={target}
          onChange={(e) => setTarget(Number(e.target.value))}
          className="ml-1 rounded border border-line bg-surface px-1.5 py-0.5"
        >
          {versions.map((v) => (
            <option key={v.id} value={v.id}>
              v{v.version_no} · {v.effective_at.slice(0, 10)}
            </option>
          ))}
        </select>
      </label>
      <DiffView diff={diff.data?.diff ?? ""} />
    </div>
  );
}
