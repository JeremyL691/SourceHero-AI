"use client";

/** Settings card: a bookmarklet that saves the current page to SourceHero. */

import { useEffect, useRef, useState } from "react";
import { buildBookmarklet } from "@/lib/bookmarklet";
import { Button, Card } from "@/components/ui";

export function BookmarkletCard() {
  const linkRef = useRef<HTMLAnchorElement>(null);
  const [bookmarklet, setBookmarklet] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const code = buildBookmarklet(window.location.origin);
    setBookmarklet(code);
    // Set href through the DOM: React warns on javascript: URLs in props.
    linkRef.current?.setAttribute("href", code);
  }, []);

  const copy = async () => {
    if (!bookmarklet) return;
    try {
      await navigator.clipboard.writeText(bookmarklet);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Card title="Save from the browser">
      <div className="space-y-3 p-4 text-sm">
        <p className="text-muted">
          Drag this link to your bookmarks bar. On any page, click it to open the capture form
          with the page URL, title and any selected text.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <a
            ref={linkRef}
            data-testid="k8-bookmarklet-link"
            className="rounded-md border border-accent px-3 py-1.5 text-sm font-medium text-accent"
          >
            Save to SourceHero
          </a>
          <Button
            type="button"
            data-testid="k8-bookmarklet-copy"
            onClick={copy}
            disabled={!bookmarklet}
          >
            {copied ? "Copied" : "Copy bookmarklet"}
          </Button>
        </div>
      </div>
    </Card>
  );
}
