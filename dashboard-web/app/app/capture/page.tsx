"use client";

/** Capture form opened by the bookmarklet. It pre-fills from the query string and
 * saves only when the user clicks "Save capture". Nothing is submitted on load. */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { Suspense, useEffect, useState } from "react";
import { api, getToken } from "@/lib/api";
import { Button, Card } from "@/components/ui";

function CaptureForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [signedIn, setSignedIn] = useState<boolean | null>(null);

  const initialUrl = params.get("url") ?? "";
  const initialTitle = params.get("title") ?? "";
  const initialText = params.get("text") ?? "";

  const [url, setUrl] = useState(initialUrl);
  const [title, setTitle] = useState(initialTitle);
  const [text, setText] = useState(initialUrl ? "" : initialText);

  useEffect(() => {
    if (getToken()) {
      setSignedIn(true);
      return;
    }
    setSignedIn(false);
    const query = new URLSearchParams();
    for (const key of ["url", "title", "text"]) {
      const value = params.get(key);
      if (value) query.set(key, value);
    }
    const next = `/app/capture${query.toString() ? `?${query}` : ""}`;
    router.replace(`/login?next=${encodeURIComponent(next)}`);
    // Run once on mount: the redirect target is fixed by the initial query string.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = useMutation({
    mutationFn: () =>
      url.trim()
        ? api.post<{ source_id: string }>("/captures", { url: url.trim() })
        : api.post<{ source_id: string }>("/captures", { text, title: title || null }),
  });

  if (signedIn !== true) {
    return <p className="p-4 text-sm text-muted">Checking your session…</p>;
  }

  const canSave = url.trim().length > 0 || text.trim().length > 0;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Save capture</h1>
        <p className="text-sm text-muted">Review what the bookmarklet sent, then save it to your library.</p>
      </header>

      <Card title="Capture">
        <form className="space-y-3 p-4" onSubmit={(event) => event.preventDefault()}>
          <div>
            <label htmlFor="capture-url" className="block text-sm font-medium">
              URL
            </label>
            <input
              id="capture-url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label htmlFor="capture-title" className="block text-sm font-medium">
              Title
            </label>
            <input
              id="capture-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
            />
          </div>
          {!url.trim() && (
            <div>
              <label htmlFor="capture-text" className="block text-sm font-medium">
                Text
              </label>
              <textarea
                id="capture-text"
                rows={6}
                value={text}
                onChange={(e) => setText(e.target.value)}
                className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
              />
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              variant="primary"
              disabled={!canSave || save.isPending || save.isSuccess}
              onClick={() => save.mutate()}
            >
              {save.isPending ? "Saving…" : "Save capture"}
            </Button>
            <Link href="/app/inbox" className="text-sm text-muted underline underline-offset-2">
              Close
            </Link>
          </div>

          {save.isSuccess && (
            <p role="status" className="text-sm text-supported">
              Captured
            </p>
          )}
          {save.isError && (
            <p role="alert" className="text-sm text-contradicted">
              {save.error instanceof Error ? save.error.message : "Could not save the capture."}
            </p>
          )}
        </form>
      </Card>
    </div>
  );
}

export default function CapturePage() {
  return (
    <Suspense fallback={<p className="p-4 text-sm text-muted">Loading…</p>}>
      <CaptureForm />
    </Suspense>
  );
}
