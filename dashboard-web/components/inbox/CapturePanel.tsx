"use client";

/** Capture panel: paste a URL or note, or drop a file. Preview first, save only on confirm. */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { ApiError, api } from "@/lib/api";
import { useToast } from "@/components/providers";
import { Button, ErrorNotice } from "@/components/ui";
import { PreviewCard, type ParsePreview } from "./PreviewCard";

const FILE_ACCEPT = ".pdf,.md,.txt,.html,.htm";

/** What the user asked to save. The preview is tied to this snapshot, so editing the input later cannot change it. */
type Pending =
  | { kind: "url"; body: { url: string } }
  | { kind: "text"; body: { text: string; title: string } }
  | { kind: "file"; file: File };

type Preview = { pending: Pending; data: ParsePreview };

const ERROR_LABEL: Record<string, string> = {
  file_too_large: "File too large",
  bad_file_type: "Unsupported file type",
  ssrf_blocked: "Link refused",
};

/** Keep the backend message (it says the limit or the refused address) and prefix a short label. */
function describeError(err: unknown): Error {
  if (err instanceof ApiError) {
    const label =
      ERROR_LABEL[err.code] ?? (err.status === 413 ? "File too large" : err.status === 415 ? "Unsupported file type" : null);
    return new Error(label ? `${label}: ${err.message}` : err.message);
  }
  return err instanceof Error ? err : new Error("Something went wrong");
}

export function CapturePanel() {
  const router = useRouter();
  const toast = useToast();
  const invalidate = useQueryClient();
  const [value, setValue] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const parse = useMutation({
    mutationFn: async (pending: Pending): Promise<ParsePreview> => {
      if (pending.kind === "file") {
        const body = new FormData();
        body.append("file", pending.file);
        return api.form<ParsePreview>("/captures/parse", body);
      }
      return api.post<ParsePreview>("/captures/parse", pending.body);
    },
    onMutate: () => setPreview(null),
    onSuccess: (data, pending) => setPreview({ pending, data }),
  });

  const save = useMutation({
    mutationFn: async (pending: Pending) => {
      if (pending.kind === "file") {
        const body = new FormData();
        body.append("file", pending.file);
        return api.form("/sources/upload", body);
      }
      return api.post("/captures", pending.body);
    },
    onSuccess: (_data, pending) => {
      setPreview(null);
      invalidate.invalidateQueries();
      if (pending.kind === "file") {
        toast("Uploaded — ingesting in the background");
      } else {
        setValue("");
        toast("Captured");
        router.refresh();
      }
    },
  });

  const startPreview = (e?: FormEvent) => {
    e?.preventDefault();
    const text = value.trim();
    if (!text) return;
    const pending: Pending = /^(https?:\/\/)/i.test(text)
      ? { kind: "url", body: { url: text } }
      : { kind: "text", body: { text, title: text.slice(0, 80) } };
    parse.mutate(pending);
  };

  const previewFile = (file: File | undefined) => {
    if (file) parse.mutate({ kind: "file", file });
  };

  const onDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(true);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    previewFile(e.dataTransfer.files?.[0]);
  };

  const busy = parse.isPending || save.isPending;

  return (
    <div className="space-y-3 p-4">
      <form onSubmit={startPreview} className="flex flex-col gap-2 sm:flex-row">
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="https://example.com/article — or just type a note"
          aria-label="URL or note"
          className="flex-1 rounded-md border border-line bg-surface px-3 py-2 text-sm"
        />
        <Button type="submit" variant="primary" disabled={busy || !value.trim()}>
          {parse.isPending ? "Previewing…" : "Capture"}
        </Button>
      </form>

      <div
        onDragOver={onDragOver}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        data-testid="drop-zone"
        className={
          "flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed px-3 py-3 text-sm " +
          (dragging ? "border-accent bg-accent-soft" : "border-line")
        }
      >
        <span className="text-muted">Drop a file here, pdf, md, txt or html, up to 25 MB.</span>
        <Button
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={busy}
          data-testid="choose-file"
        >
          Choose file
        </Button>
        <input
          ref={fileInput}
          type="file"
          accept={FILE_ACCEPT}
          aria-label="Upload a file"
          className="hidden"
          onChange={(e) => {
            previewFile(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </div>

      {parse.error && <ErrorNotice error={describeError(parse.error)} />}
      {save.error && <ErrorNotice error={describeError(save.error)} />}

      {preview && (
        <PreviewCard
          kind={preview.pending.kind}
          data={preview.data}
          busy={save.isPending}
          onCancel={() => setPreview(null)}
          onConfirm={() => save.mutate(preview.pending)}
        />
      )}
    </div>
  );
}
