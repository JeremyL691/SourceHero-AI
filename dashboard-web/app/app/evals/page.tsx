"use client";

/** Evals: benchmark history and metrics across suites. */

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { EvalRun } from "@/lib/types";
import { Card, EmptyState, ErrorNotice, Skeleton } from "@/components/ui";

export default function EvalsPage() {
  const runs = useQuery({
    queryKey: ["eval-runs"],
    queryFn: () => api.get<EvalRun[]>("/evals/runs?limit=50"),
  });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Evaluation Runs</h1>
        <p className="text-sm text-muted">
          Offline benchmarks, retrieval quality, claim extraction, and verification metrics.
        </p>
      </header>

      <Card>
        {runs.isLoading ? (
          <Skeleton rows={6} />
        ) : runs.error ? (
          <div className="p-4">
            <ErrorNotice error={runs.error} onRetry={() => runs.refetch()} />
          </div>
        ) : (runs.data ?? []).length === 0 ? (
          <EmptyState
            title="No evaluation runs recorded"
            hint="Run `make eval` or execute the benchmark suites to record quality metrics."
          />
        ) : (
          <ul className="divide-y divide-line">
            {(runs.data ?? []).map((run) => (
              <li key={run.id} className="p-4 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="mono text-xs font-semibold px-2 py-0.5 rounded bg-accent-soft text-accent">
                      {run.suite}
                    </span>
                    <span className="text-xs text-muted">mode: {run.mode}</span>
                  </div>
                  <span className="text-xs text-muted">
                    {run.created_at ? run.created_at.slice(0, 19).replace("T", " ") : ""}
                  </span>
                </div>

                <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-4">
                  {Object.entries(run.metrics ?? {}).map(([metric, val]) => {
                    const display =
                      typeof val === "number"
                        ? val < 1 && val > 0
                          ? val.toFixed(4)
                          : val
                        : String(val);
                    return (
                      <div key={metric} className="rounded border border-line bg-surface p-2.5">
                        <dt className="text-[11px] text-muted truncate">{metric}</dt>
                        <dd className="mono text-base font-semibold text-ink mt-0.5">
                          {display}
                        </dd>
                      </div>
                    );
                  })}
                </div>

                {run.config && Object.keys(run.config).length > 0 && (
                  <div className="flex flex-wrap gap-2 text-[11px] text-muted">
                    {Object.entries(run.config).map(([k, v]) => (
                      <span key={k} className="rounded bg-paper px-1.5 py-0.5">
                        {k}: {String(v)}
                      </span>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
