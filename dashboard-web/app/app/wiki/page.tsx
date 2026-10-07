"use client";

/** The wiki: compiled pages, their lint state, and an export. */

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Download } from "lucide-react";
import { api } from "@/lib/api";
import type { LintFinding, WikiPage } from "@/lib/types";
import { Button, Card, EmptyState, ErrorNotice, Skeleton, StatusDot } from "@/components/ui";

export default function WikiIndex() {
  const pages = useQuery({ queryKey: ["wiki-pages"], queryFn: () => api.get<WikiPage[]>("/wiki/pages") });
  const findings = useQuery({
    queryKey: ["wiki-lint"],
    queryFn: () => api.get<{ items: LintFinding[] }>("/wiki/lint"),
  });

  const [exporting, setExporting] = useState(false);

  const open = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const blob = await api.blob("/wiki/export");
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "wiki-export.zip";
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      console.error("Export failed", e);
    } finally {
      setExporting(false);
    }
  };

  const openFindings = (findings.data?.items ?? []).filter((f) => !f.resolved_at);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="serif text-2xl font-semibold">Wiki</h1>
          <p className="text-sm text-muted">
            {pages.data?.length ?? 0} pages, compiled from claims.
          </p>
        </div>
        <Button onClick={open}>
          <Download className="h-4 w-4" aria-hidden />
          Export
        </Button>
      </header>

      <Card title="Pages">
        {pages.isLoading ? (
          <Skeleton rows={5} />
        ) : pages.error ? (
          <div className="p-4">
            <ErrorNotice error={pages.error} onRetry={() => pages.refetch()} />
          </div>
        ) : (pages.data ?? []).length === 0 ? (
          <EmptyState
            title="No pages yet"
            hint="Once sources are ingested and clustered, SourceHero compiles a page per topic."
          />
        ) : (
          <ul className="divide-y divide-line">
            {(pages.data ?? []).map((page) => (
              <li key={page.id} className="flex items-center justify-between gap-3 px-4 py-2">
                <Link href={`/app/wiki/${page.slug}`} className="serif text-sm hover:text-accent">
                  {page.title}
                </Link>
                <div className="flex items-center gap-2 text-xs text-muted">
                  <span>rev {page.revision}</span>
                  <span>{page.sentence_count} sentences</span>
                  {page.lint_count > 0 && (
                    <span className="text-disputed">{page.lint_count} lint</span>
                  )}
                  <StatusDot status={page.status} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {openFindings.length > 0 && (
        <Card title={`Lint (${openFindings.length})`}>
          <ul className="divide-y divide-line">
            {openFindings.map((finding) => (
              <li key={finding.id} className="flex items-center justify-between gap-3 px-4 py-2 text-sm">
                <span className="mono text-xs">{finding.kind}</span>
                <span className={`text-xs ${finding.severity === "error" ? "text-contradicted" : "text-disputed"}`}>
                  {finding.severity}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
