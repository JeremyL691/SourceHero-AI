"use client";

/** Preview of a capture or upload, shown before anything is saved. */

import { Button, Card } from "@/components/ui";

export type CaptureKind = "url" | "text" | "file";

/** Shape returned by POST /v1/captures/parse. Every field is optional because each kind returns a subset. */
export type ParsePreview = {
  title?: string;
  title_guess?: string;
  filename?: string;
  url?: string;
  sentences?: number;
  candidate_claims?: number;
  pages?: number | null;
  chars?: number;
  preview?: string;
  warnings?: string[];
  error?: string;
};

const KIND_LABEL: Record<CaptureKind, string> = {
  url: "Web page",
  text: "Note",
  file: "File",
};

export function PreviewCard({
  kind,
  data,
  busy,
  onConfirm,
  onCancel,
}: {
  kind: CaptureKind;
  data: ParsePreview;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const title = data.title ?? data.title_guess ?? data.filename ?? data.url ?? "Untitled";
  const counts: [string, number][] = [];
  if (data.sentences != null) counts.push(["Sentences", data.sentences]);
  if (data.candidate_claims != null) counts.push(["Candidate claims", data.candidate_claims]);
  if (data.pages != null) counts.push(["Pages", data.pages]);
  if (data.chars != null) counts.push(["Characters", data.chars]);

  return (
    <Card
      title="Preview"
      className="k6-preview"
      action={
        <div className="flex gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={busy} data-testid="k6-capture-cancel">
            Cancel
          </Button>
          <Button variant="primary" onClick={onConfirm} disabled={busy} data-testid="k6-capture-confirm">
            {busy ? "Saving…" : kind === "file" ? "Save upload" : "Save capture"}
          </Button>
        </div>
      }
    >
      <div className="space-y-3 p-4 text-sm" data-testid="k6-capture-preview">
        <p className="font-medium text-ink">{title}</p>
        <p className="text-xs text-muted">{KIND_LABEL[kind]}</p>

        {counts.length > 0 && (
          <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
            {counts.map(([label, value]) => (
              <div key={label} className="flex gap-1.5">
                <dt className="text-muted">{label}</dt>
                <dd className="font-medium text-ink">{value}</dd>
              </div>
            ))}
          </dl>
        )}

        {data.error && (
          <p className="rounded-md bg-disputed-soft px-3 py-2 text-xs text-disputed">
            The page could not be read in preview ({data.error}). Saving will still queue it.
          </p>
        )}

        {(data.warnings ?? []).length > 0 && (
          <ul className="list-disc space-y-1 pl-5 text-xs text-disputed">
            {(data.warnings ?? []).map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        )}

        {data.preview ? (
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-paper p-3 text-xs">
            {data.preview}
          </pre>
        ) : (
          <p className="text-xs text-muted">No text to preview.</p>
        )}
      </div>
    </Card>
  );
}
