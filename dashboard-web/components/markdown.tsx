"use client";

/** Markdown rendering with SourceHero's footnote and citation conventions.
 *
 * `[^12]` footnotes (compiled wiki pages) become hoverable chips showing the source
 * quote, and `[1]`-style inline citations are rendered as citations rather than left
 * as literal brackets. */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { WikiFootnote } from "@/lib/types";

export function MarkdownView({
  children,
  footnotes = {},
}: {
  children: string;
  footnotes?: Record<number, WikiFootnote>;
}) {
  return (
    <div className="prose text-sm">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children: kids, ...rest }) => {
            if ("data-footnote-ref" in rest || href?.includes("user-content-fn-")) {
              const n = Number(String(kids).replace(/\D/g, ""));
              const fn = footnotes[n];
              return (
                <span className="group relative inline-block align-super text-xs">
                  <a
                    href={href}
                    className="mono inline-block rounded bg-accent-soft px-1 text-[11px] text-accent no-underline transition-colors hover:bg-accent hover:text-white"
                  >
                    [{n || kids}]
                  </a>
                  {fn && (
                    <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1 hidden w-72 -translate-x-1/2 rounded-md border border-line bg-surface p-2.5 text-left text-xs font-normal normal-case text-ink shadow-lg group-hover:pointer-events-auto group-hover:block group-focus-within:pointer-events-auto group-focus-within:block">
                      <span className="block font-medium text-ink">
                        {fn.document_title || `Claim #${fn.claim_id}`}
                      </span>
                      {fn.quote && (
                        <span className="serif mt-1 block italic text-muted">
                          &ldquo;{fn.quote}&rdquo;
                        </span>
                      )}
                      {fn.url && (
                        <a
                          href={fn.url}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="mt-1 block truncate text-[11px] text-accent underline underline-offset-2"
                        >
                          {fn.url}
                        </a>
                      )}
                    </span>
                  )}
                </span>
              );
            }
            return (
              <a
                href={href}
                className="text-accent underline underline-offset-2"
                target="_blank"
                rel="noreferrer noopener"
              >
                {kids}
              </a>
            );
          },
          code: ({ className, children: kids }) =>
            className ? (
              <code className="mono text-xs">{kids}</code>
            ) : (
              <code>{kids}</code>
            ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
