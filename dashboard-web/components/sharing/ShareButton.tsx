"use client";

/** "Share" for one wiki page or answer. The link is shown once: only its hash is stored. */

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Check, Copy, Share2 } from "lucide-react";
import { api } from "@/lib/api";
import { Button, ErrorNotice } from "@/components/ui";

type ShareKind = "wiki_page" | "answer";

type ShareCreated = {
  id: number;
  kind: ShareKind;
  target_id: string;
  token: string;
  created_at: string;
  expires_at: string | null;
};

export function ShareButton({ kind, targetId }: { kind: ShareKind; targetId: string }) {
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const share = useMutation({
    mutationFn: () =>
      api.post<ShareCreated>("/shares", { kind, target_id: targetId, expires_in_days: null }),
    onSuccess: (created) => {
      setLink(`${window.location.origin}/share/${created.token}`);
      setCopied(false);
    },
  });

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      /* clipboard can be blocked; the link is still visible to copy by hand */
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <Button
        data-testid="share-button"
        onClick={() => share.mutate()}
        disabled={share.isPending}
      >
        <Share2 className="h-4 w-4" aria-hidden />
        Share
      </Button>
      {share.error && <ErrorNotice error={share.error} />}
      {link && (
        <div data-testid="share-result" className="flex flex-col gap-2 rounded-md border border-line p-3 text-sm">
          <p className="text-muted">
            This read-only link is shown only once. Copy it now. You can revoke it later under
            Shares.
          </p>
          <div className="flex items-center gap-2">
            <code data-testid="share-link" className="flex-1 break-all rounded bg-line/40 px-2 py-1">
              {link}
            </code>
            <Button data-testid="share-copy" onClick={copy}>
              {copied ? <Check className="h-4 w-4" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
