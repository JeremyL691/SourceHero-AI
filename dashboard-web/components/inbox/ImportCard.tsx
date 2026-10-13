"use client";

/** Bulk import: an OPML subscription list or a highlights CSV. */

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useToast } from "@/components/providers";
import { Button } from "@/components/ui";

type ImportError = { code: string; url?: string | null; row?: number | null };
type ImportResult = { created: number; skipped: number; errors: ImportError[] };

type ImportKind = "opml" | "highlights";

const LABELS: Record<ImportKind, { field: string; accept: string; submit: string }> = {
  opml: { field: "OPML subscription list", accept: ".opml,.xml", submit: "Import feeds" },
  highlights: { field: "Highlights CSV", accept: ".csv", submit: "Import highlights" },
};

function ImportForm({ kind }: { kind: ImportKind }) {
  const toast = useToast();
  const invalidate = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const label = LABELS[kind];

  const run = useMutation({
    mutationFn: (picked: File) => {
      const body = new FormData();
      body.append("file", picked);
      return api.form<ImportResult>(`/imports/${kind}`, body);
    },
    onMutate: () => setResult(null),
    onSuccess: (data) => {
      setResult(data);
      invalidate.invalidateQueries();
      toast(`Imported ${data.created} new, ${data.skipped} skipped`);
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Import failed"),
  });

  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (file) run.mutate(file);
  };

  return (
    <form onSubmit={submit} data-testid={`${kind}-form`} className="space-y-2">
      <label className="block text-sm">
        <span className="text-muted">{label.field}</span>
        <input
          type="file"
          accept={label.accept}
          data-testid={`${kind}-file`}
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setResult(null);
          }}
          className="mt-1 block w-full text-sm"
        />
      </label>
      <Button type="submit" variant="primary" disabled={!file || run.isPending} data-testid={`${kind}-submit`}>
        {run.isPending ? "Importing…" : label.submit}
      </Button>

      {result && (
        <div data-testid={`${kind}-result`} className="text-sm">
          <p>
            <span data-testid={`${kind}-created`}>{result.created}</span> created ·{" "}
            <span data-testid={`${kind}-skipped`}>{result.skipped}</span> skipped
          </p>
          {result.errors.length > 0 && (
            <ul data-testid={`${kind}-errors`} className="mt-1 list-disc pl-5 text-contradicted">
              {result.errors.map((err, i) => (
                <li key={i}>
                  {err.row != null ? `Row ${err.row}: ` : ""}
                  {err.url ? `${err.url} — ` : ""}
                  {err.code}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </form>
  );
}

export function ImportCard() {
  return (
    <div data-testid="import-card" className="grid gap-6 p-4 sm:grid-cols-2">
      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Import feeds (OPML)</h3>
        <ImportForm kind="opml" />
      </section>
      <section className="space-y-2">
        <h3 className="text-sm font-semibold">Import highlights (CSV)</h3>
        <ImportForm kind="highlights" />
      </section>
    </div>
  );
}
