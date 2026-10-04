"use client";

/** Verify: paste prose, see which sentences the sources can back up. */

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { CheckCheck } from "lucide-react";
import { api } from "@/lib/api";
import type { VerifyResponse } from "@/lib/types";
import { Button, Card, EmptyState, VerdictBadge } from "@/components/ui";
import { CitationList } from "@/components/verdict";

const SAMPLE =
  "The HX-2 cell delivers an energy density of 450 Wh/kg. " +
  "Orchid-7 was trained on 2 trillion tokens. " +
  "The congestion charge costs drivers $9 per day. " +
  "Bananas are yellow in the tropics.";

export default function VerifyPage() {
  const [text, setText] = useState(SAMPLE);
  const [result, setResult] = useState<VerifyResponse | null>(null);

  const run = useMutation({
    mutationFn: () => api.post<VerifyResponse>("/verify", { text, k: 5 }),
    onSuccess: setResult,
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Verify</h1>
        <p className="text-sm text-muted">
          Paste text and SourceHero checks each sentence against what your sources say.
        </p>
      </header>

      <Card>
        <div className="space-y-3 p-4">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={5}
            aria-label="Text to verify"
            className="serif w-full rounded-md border border-line bg-surface p-3 text-sm"
          />
          <div className="flex items-center gap-3">
            <Button
              variant="primary"
              onClick={() => run.mutate()}
              disabled={run.isPending || !text.trim()}
            >
              <CheckCheck className="h-4 w-4" aria-hidden />
              {run.isPending ? "Checking…" : "Verify"}
            </Button>
            {run.error && (
              <span role="alert" className="text-sm text-contradicted">
                {run.error instanceof Error ? run.error.message : String(run.error)}
              </span>
            )}
          </div>
        </div>
      </Card>

      {result && (
        <>
          <div className="flex flex-wrap gap-2">
            {(["supported", "contradicted", "disputed", "unsupported"] as const).map((verdict) => (
              <span key={verdict} className="flex items-center gap-1.5 text-xs text-muted">
                <VerdictBadge verdict={verdict} small />
                {result.summary[verdict] ?? 0}
              </span>
            ))}
            <span className="ml-auto text-xs text-muted">{result.method}</span>
          </div>

          <div className="space-y-3">
            {result.sentences.map((sentence) => (
              <Card key={sentence.index}>
                <div className="space-y-3 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <p className="serif flex-1 text-sm">{sentence.text}</p>
                    <VerdictBadge verdict={sentence.verdict} small />
                  </div>
                  {sentence.rationale && (
                    <p className="text-xs text-muted">{sentence.rationale}</p>
                  )}
                  <div className="grid gap-3 sm:grid-cols-2">
                    {sentence.supporting.length > 0 && (
                      <div>
                        <h3 className="mb-1 text-[11px] tracking-wide text-supported uppercase">
                          Supported by
                        </h3>
                        <CitationList citations={sentence.supporting} />
                      </div>
                    )}
                    {sentence.contradicting.length > 0 && (
                      <div>
                        <h3 className="mb-1 text-[11px] tracking-wide text-contradicted uppercase">
                          Contradicted by
                        </h3>
                        <CitationList citations={sentence.contradicting} />
                      </div>
                    )}
                  </div>
                </div>
              </Card>
            ))}
          </div>
        </>
      )}

      {!result && (
        <Card>
          <EmptyState
            title="Nothing verified yet"
            hint="The sample text is pre-filled so you can see how each sentence is judged."
          />
        </Card>
      )}
    </div>
  );
}
