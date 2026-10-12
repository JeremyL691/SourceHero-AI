"use client";

/** Public, read-only view of a shared wiki page or answer. No login, no bearer token. */

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { API_URL } from "@/lib/api";
import { Card, EmptyState, ErrorNotice, Skeleton, VerdictBadge } from "@/components/ui";

type Sentence = { text: string; verdict: string; citations: number[] };
type Footnote = { n: number; quote: string; document_title: string; url: string | null };
type Citation = { n: number; title: string; url: string | null; date: string | null; quote: string };

type WikiShare = {
  kind: "wiki_page";
  slug: string;
  title: string;
  summary: Sentence[];
  sections: { heading: string; sentences: Sentence[] }[];
  footnotes: Footnote[];
};

type AnswerShare = {
  kind: "answer";
  question: string;
  answer_md: string;
  sentences: Sentence[];
  citations: Citation[];
};

type SharedSnapshot = WikiShare | AnswerShare;

async function fetchShare(token: string): Promise<SharedSnapshot> {
  // Deliberately a plain fetch: a shared link must work without a session.
  const response = await fetch(`${API_URL}/v1/public/shares/${encodeURIComponent(token)}`, {
    credentials: "omit",
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(response.status === 404 ? "This link is invalid, expired, or was revoked." : "Could not load this link.");
  }
  return (await response.json()) as SharedSnapshot;
}

function Sentences({ items }: { items: Sentence[] }) {
  return (
    <p className="text-sm leading-relaxed text-ink">
      {items.map((s, i) => (
        <span key={i}>
          {s.verdict && <VerdictBadge verdict={s.verdict} small />} {s.text}{" "}
        </span>
      ))}
    </p>
  );
}

export default function SharedView() {
  const { token } = useParams<{ token: string }>();
  const share = useQuery({
    queryKey: ["public-share", token],
    queryFn: () => fetchShare(token),
    retry: false,
  });

  return (
    <main id="main" className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-8" data-testid="k8-shared-view">
      {share.isLoading && <Skeleton rows={4} />}
      {share.error && <ErrorNotice error={share.error} />}
      {share.data?.kind === "wiki_page" && <WikiView page={share.data} />}
      {share.data?.kind === "answer" && <AnswerView answer={share.data} />}
      {share.data && !["wiki_page", "answer"].includes(share.data.kind) && (
        <EmptyState title="Nothing to show" />
      )}
    </main>
  );
}

function WikiView({ page }: { page: WikiShare }) {
  return (
    <>
      <h1 className="text-2xl font-semibold text-ink" data-testid="k8-shared-title">
        {page.title}
      </h1>
      <p className="text-xs text-muted">Read-only shared wiki page</p>
      <Card>
        <div className="flex flex-col gap-4 p-4">
          {page.summary.length > 0 && <Sentences items={page.summary} />}
          {page.sections.map((section) => (
            <section key={section.heading} className="flex flex-col gap-2">
              <h2 className="text-sm font-semibold text-ink">{section.heading}</h2>
              <Sentences items={section.sentences} />
            </section>
          ))}
        </div>
      </Card>
      {page.footnotes.length > 0 && (
        <Card>
          <ol className="flex flex-col gap-2 p-4 text-sm text-muted">
            {page.footnotes.map((fn) => (
              <li key={fn.n}>
                <span className="font-medium text-ink">[{fn.n}]</span> {fn.document_title}
                {fn.url && (
                  <>
                    {" "}
                    <a href={fn.url} rel="noopener noreferrer" className="underline">
                      source
                    </a>
                  </>
                )}
                {fn.quote && <blockquote className="mt-1 border-l-2 border-line pl-2">{fn.quote}</blockquote>}
              </li>
            ))}
          </ol>
        </Card>
      )}
    </>
  );
}

function AnswerView({ answer }: { answer: AnswerShare }) {
  return (
    <>
      <h1 className="text-2xl font-semibold text-ink" data-testid="k8-shared-question">
        {answer.question}
      </h1>
      <p className="text-xs text-muted">Read-only shared answer</p>
      <Card>
        <div className="p-4">
          <Sentences items={answer.sentences} />
        </div>
      </Card>
      {answer.citations.length > 0 && (
        <Card>
          <ol className="flex flex-col gap-2 p-4 text-sm text-muted">
            {answer.citations.map((c) => (
              <li key={c.n}>
                <span className="font-medium text-ink">[{c.n}]</span> {c.title}
                {c.date && <> · {c.date}</>}
                {c.url && (
                  <>
                    {" "}
                    <a href={c.url} rel="noopener noreferrer" className="underline">
                      source
                    </a>
                  </>
                )}
                {c.quote && <blockquote className="mt-1 border-l-2 border-line pl-2">{c.quote}</blockquote>}
              </li>
            ))}
          </ol>
        </Card>
      )}
    </>
  );
}
