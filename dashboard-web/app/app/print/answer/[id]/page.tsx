"use client";

/** Print view of one answer's evidence brief. Same content as the Markdown export. */

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { ErrorNotice, Skeleton } from "@/components/ui";
import { MarkdownView } from "@/components/markdown";

const PRINT_CSS = `
@media print {
  body * { visibility: hidden !important; }
  #print-root, #print-root * { visibility: visible !important; }
  #print-root { position: absolute; left: 0; top: 0; width: 100%; padding: 0; }
  .print-toolbar { display: none !important; }
}
`;

export default function PrintAnswer() {
  const { id } = useParams<{ id: string }>();

  const brief = useQuery({
    queryKey: ["print-answer", id],
    queryFn: async () => (await api.blob(`/answers/${id}/export?format=md`)).text(),
  });

  return (
    <div className="space-y-4">
      <style>{PRINT_CSS}</style>
      <div className="print-toolbar flex items-center gap-2">
        <button
          type="button"
          data-testid="print-button"
          onClick={() => window.print()}
          className="rounded-md border border-line px-3 py-1.5 text-sm"
        >
          Print
        </button>
      </div>
      {brief.error ? (
        <ErrorNotice error={brief.error} />
      ) : brief.isLoading ? (
        <Skeleton rows={8} />
      ) : (
        <article id="print-root" data-testid="print-root" className="serif bg-surface p-6">
          <MarkdownView>{brief.data ?? ""}</MarkdownView>
        </article>
      )}
    </div>
  );
}
