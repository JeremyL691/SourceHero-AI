"use client";

/** Ask: a streamed, per-sentence verified answer.
 *
 * The stream delivers `retrieval` -> `conflicts` -> (`delta` | `sentence`)* -> `done`,
 * and each `sentence` frame already carries that sentence's verdict. So a claim is
 * checked while it is being read, not after the whole answer arrives. */

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Send, Square } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, streamSSE } from "@/lib/api";
import type { AnswerRecord, Citation, SearchHit, Verdict } from "@/lib/types";
import { Button, Card, EmptyState, Skeleton, VerdictBadge } from "@/components/ui";
import { CitationList } from "@/components/verdict";
import { ShareButton } from "@/components/sharing/ShareButton";

interface Streamed {
  index: number;
  text: string;
  verdict: Verdict;
  confidence: number;
  citations: number[];
  flags: string[];
}

interface Dispute {
  label?: string;
  a?: string;
  b?: string;
}

function toCitations(hits: SearchHit[]): Citation[] {
  return hits.map((hit, i) => ({
    n: i + 1,
    chunk_id: hit.chunk_id,
    document_id: hit.document_id,
    version_id: hit.version_id,
    title: hit.title,
    url: hit.url,
    source: hit.source_name || (hit as unknown as { source?: string }).source || hit.title,
    date: hit.published_at ? hit.published_at.slice(0, 10) : null,
    quote: hit.text,
    char_start: hit.char_start,
    char_end: hit.char_end,
    page: hit.page,
    score: hit.scores?.rrf ?? 0,
  }));
}

/** Fetch a Markdown export with the bearer token and save it as a file. */
async function downloadBrief(path: string, filename: string) {
  const blob = await api.blob(path);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export default function AskPage() {
  const router = useRouter();
  const [question, setQuestion] = useState("");
  const [text, setText] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [conflicts, setConflicts] = useState<Dispute[]>([]);
  const [sentences, setSentences] = useState<Streamed[]>([]);
  const [pinned, setPinned] = useState<Citation[] | null>(null);
  const [currentAnswerId, setCurrentAnswerId] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState(false);
  const [savingNote, setSavingNote] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      abort.current?.abort();
    };
  }, []);

  const history = useQuery({
    queryKey: ["answers"],
    queryFn: () => api.get<AnswerRecord[]>("/answers"),
  });

  const citations = useMemo(() => pinned ?? toCitations(hits), [pinned, hits]);

  const openCitation = useCallback(
    (cOrN: Citation | number) => {
      const target = typeof cOrN === "number" ? citations.find((c) => c.n === cOrN) : cOrN;
      if (target && target.document_id) {
        const query = new URLSearchParams();
        if (target.version_id) query.set("v", String(target.version_id));
        if (target.char_start != null) query.set("start", String(target.char_start));
        if (target.char_end != null) query.set("end", String(target.char_end));
        router.push(`/app/library/${target.document_id}?${query.toString()}`);
      }
    },
    [citations, router],
  );

  const saveAsNote = useCallback(async () => {
    if (!currentAnswerId || savingNote) return;
    setSavingNote(true);
    try {
      await api.post(`/answers/${currentAnswerId}/save-note`);
      setSavedNote(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingNote(false);
    }
  }, [currentAnswerId, savingNote]);

  const submit = useCallback(
    async (event?: React.FormEvent) => {
      event?.preventDefault();
      const q = question.trim();
      if (!q || running) return;

      setRunning(true);
      setError(null);
      setText("");
      setHits([]);
      setConflicts([]);
      setSentences([]);
      setPinned(null);
      setCurrentAnswerId(null);
      setSavedNote(false);

      const controller = new AbortController();
      abort.current = controller;

      try {
        await streamSSE(
          "/ask",
          { question: q, k: 8 },
          (event, data) => {
            const payload = data as Record<string, unknown>;
            if (event === "retrieval") {
              setHits((payload.hits as SearchHit[]) ?? []);
            } else if (event === "conflicts") {
              setConflicts((payload.disputes as Dispute[]) ?? []);
            } else if (event === "delta") {
              setText((current) => current + String(payload.text ?? ""));
            } else if (event === "sentence") {
              setSentences((current) => [
                ...current,
                {
                  index: Number(payload.index),
                  text: String(payload.text ?? ""),
                  verdict: payload.verdict as Verdict,
                  confidence: Number(payload.confidence ?? 0),
                  citations: (payload.citations as number[]) ?? [],
                  flags: (payload.flags as string[]) ?? [],
                },
              ]);
            } else if (event === "done") {
              if (payload.answer_id) {
                setCurrentAnswerId(String(payload.answer_id));
              }
            }
          },
          controller.signal,
        );
      } catch (e) {
        // An abort is a user action, not a failure worth reporting.
        if ((e as Error).name !== "AbortError") {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        setRunning(false);
        abort.current = null;
        history.refetch();
      }
    },
    [question, running, history],
  );

  const exportBrief = useCallback(async () => {
    if (!currentAnswerId) return;
    try {
      await downloadBrief(
        `/answers/${currentAnswerId}/export?format=md`,
        `answer-${currentAnswerId}.md`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Export failed");
    }
  }, [currentAnswerId]);

  return (
    <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
      <div className="space-y-4">
        <header>
          <h1 className="serif text-2xl font-semibold">Ask</h1>
          <p className="text-sm text-muted">
            Every sentence comes back with a verdict and the evidence behind it.
          </p>
        </header>

        <Card>
          <form onSubmit={submit} className="flex flex-col gap-2 p-4 sm:flex-row">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="What is the energy density of the HX-2?"
              aria-label="Your question"
              className="flex-1 rounded-md border border-line bg-surface px-3 py-2 text-sm"
            />
            {running ? (
              <Button variant="danger" onClick={() => abort.current?.abort()}>
                <Square className="h-4 w-4" aria-hidden />
                Stop
              </Button>
            ) : (
              <Button type="submit" variant="primary" disabled={!question.trim()}>
                <Send className="h-4 w-4" aria-hidden />
                Ask
              </Button>
            )}
          </form>
        </Card>

        {conflicts.length > 0 && (
          <div
            role="status"
            className="flex items-start gap-2 rounded-md border border-disputed/40 bg-disputed-soft px-3 py-2 text-sm text-disputed"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <div>
              <p className="font-medium">Your sources disagree on this.</p>
              <ul className="mt-1 space-y-0.5 text-xs">
                {conflicts.slice(0, 3).map((c, i) => (
                  <li key={i}>
                    {c.label ? <strong>{c.label}: </strong> : null}
                    &ldquo;{c.a}&rdquo; vs &ldquo;{c.b}&rdquo;
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}

        {error && (
          <p
            role="alert"
            className="rounded-md border border-contradicted/40 bg-contradicted-soft px-3 py-2 text-sm text-contradicted"
          >
            {error}
          </p>
        )}

        {running && !text && sentences.length === 0 && <Skeleton rows={4} />}

        {(text || sentences.length > 0) && (
          <Card
            title="Answer"
            action={
              currentAnswerId ? (
                <div className="flex items-center gap-2">
                  <Button
                    variant="default"
                    disabled={savedNote || savingNote}
                    onClick={saveAsNote}
                    className="text-xs"
                  >
                    {savedNote ? "Saved as note" : savingNote ? "Saving..." : "Save as note"}
                  </Button>
                  <Button variant="default" onClick={exportBrief} className="text-xs">
                    Export brief
                  </Button>
                  {currentAnswerId ? <ShareButton kind="answer" targetId={currentAnswerId} /> : null}
                </div>
              ) : null
            }
          >
            <div className="space-y-2 p-4">
              {sentences.length === 0 && (
                <p className="serif text-sm whitespace-pre-wrap">{text}</p>
              )}
              {sentences.map((sentence) => (
                <p key={sentence.index} className="serif text-sm leading-relaxed">
                  {sentence.text}{" "}
                  <span className="ml-1 inline-flex items-center gap-1 align-middle">
                    <VerdictBadge verdict={sentence.verdict} small />
                    {sentence.citations.map((n) => (
                      <button
                        key={n}
                        type="button"
                        onClick={() => openCitation(n)}
                        className="mono cursor-pointer text-[11px] text-accent hover:underline"
                        title="View citation source"
                      >
                        [{n}]
                      </button>
                    ))}
                    {sentence.flags.includes("uncited") && (
                      <span className="text-[11px] text-unsupported">uncited</span>
                    )}
                    {sentence.flags.includes("invalid_citation") && (
                      <span className="text-[11px] text-contradicted">bad citation</span>
                    )}
                  </span>
                </p>
              ))}
            </div>
          </Card>
        )}

        {!text && !running && sentences.length === 0 && (
          <Card>
            <EmptyState
              title="Ask a question about your sources"
              hint="Each sentence arrives with a verdict, and every citation opens the original text."
            />
          </Card>
        )}

        <Card title="Previous answers">
          {history.isLoading ? (
            <Skeleton rows={3} />
          ) : (history.data ?? []).length === 0 ? (
            <EmptyState title="No answers yet" />
          ) : (
            <ul className="divide-y divide-line">
              {(history.data ?? []).map((answer) => (
                <li key={answer.id} className="px-4 py-2">
                  <button
                    onClick={() => {
                      setQuestion(answer.question);
                      setText(answer.answer_md);
                      setSentences(
                        answer.sentences.map((s) => ({
                          index: s.index,
                          text: s.text,
                          verdict: s.verdict,
                          confidence: s.confidence,
                          citations: s.citations,
                          flags: s.flags,
                        })),
                      );
                      setPinned(answer.citations);
                      setCurrentAnswerId(answer.id);
                      setSavedNote(false);
                    }}
                    className="serif block w-full truncate text-left text-sm hover:text-accent"
                  >
                    {answer.question}
                  </button>
                  <p className="text-[11px] text-muted">
                    {answer.created_at ? answer.created_at.slice(0, 10) : ""} · {answer.method}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title={`Evidence (${citations.length})`}>
        <div className="max-h-[40rem] overflow-y-auto p-3">
          <CitationList citations={citations} onOpen={openCitation} />
        </div>
      </Card>
    </div>
  );
}
