"use client";

/** Markdown rendering with SourceHero's footnote and citation conventions.
 *
 * `[^12]` footnotes (compiled wiki pages) become hoverable chips showing the source
 * quote, and `[1]`-style inline citations are rendered as citations rather than left
 * as literal brackets. */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation } from "@/lib/types";

export function MarkdownView({
  children,
  footnotes = {},
}: {
  children: string;
  /** claim id -> the quote behind it, keyed by the footnote number. */
  footnotes?: Record<number, Citation>;
}) {
  return (
    <div className="prose text-sm">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children: kids }) => (
            <a
              href={href}
              className="text-accent underline underline-offset-2"
              target="_blank"
              rel="noreferrer noopener"
            >
              {kids}
            </a>
          ),
          code: ({ className, children: kids }) =>
            className ? (
              <code className="mono text-xs">{kids}</code>
            ) : (
              <code>{kids}</code>
            ),
        }}
      >
        {rewriteFootnotes(children, footnotes)}
      </ReactMarkdown>
    </div>
  );
}

/** Replace `[^n]` with a labelled span the stylesheet renders as a superscript chip. */
function rewriteFootnotes(markdown: string, footnotes: Record<number, Citation>): string {
  if (!Object.keys(footnotes).length) return markdown;
  return markdown.replace(/\[\^(\d+)\]/g, (_match, raw: string) => {
    const n = Number(raw);
    const citation = footnotes[n];
    const title = citation
      ? `${citation.source || citation.title}${citation.date ? ` · ${citation.date}` : ""}\n\n"${citation.quote}"`
      : `Source claim ${n}`;
    return `[^${n}: ${escapeCell(title)}]`;
  });
}

function escapeCell(text: string): string {
  return text.replace(/\|/g, "\\|").replace(/\n/g, " ");
}
