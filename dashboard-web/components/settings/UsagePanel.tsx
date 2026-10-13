"use client";

/** Usage tab: today's budget against the limits, then the 7-day totals, daily bars and per-task rows. */

import { Card, Skeleton } from "@/components/ui";
import type { UsageQuery } from "@/components/settings/types";

function budgetPercent(used: number, budget: number): number {
  return budget > 0 ? Math.min(100, (used / budget) * 100) : 0;
}

export function UsagePanel({ usage }: { usage: UsageQuery }) {
  if (usage.isLoading) return <Skeleton rows={6} />;

  const today = usage.data?.today;
  const byDay = Object.entries(usage.data?.by_day ?? {});
  const max = Math.max(1, ...byDay.map(([, v]) => v.completion_tokens + v.prompt_tokens));

  return (
    <div className="space-y-4">
      {today && (
        <Card title="Today (UTC)">
          <dl data-testid="usage-today" className="grid gap-4 p-4 text-sm sm:grid-cols-2">
            {[
              {
                label: "Requests",
                used: today.requests,
                budget: today.request_budget,
              },
              { label: "Tokens", used: today.tokens, budget: today.token_budget },
            ].map((row) => (
              <div key={row.label}>
                <dt className="text-xs text-muted">{row.label}</dt>
                <dd className="text-lg font-semibold">
                  {row.used.toLocaleString()} / {row.budget.toLocaleString()}
                </dd>
                <div className="mt-1 h-2 overflow-hidden rounded bg-line/50">
                  <div
                    className="h-full rounded bg-accent"
                    style={{ width: `${budgetPercent(row.used, row.budget)}%` }}
                  />
                </div>
              </div>
            ))}
          </dl>
        </Card>
      )}

      <Card title="Totals">
        <dl className="grid grid-cols-2 gap-3 p-4 text-sm sm:grid-cols-4">
          {[
            ["Calls", usage.data?.totals.calls ?? 0],
            ["Prompt tokens", usage.data?.totals.prompt_tokens ?? 0],
            ["Completion tokens", usage.data?.totals.completion_tokens ?? 0],
            ["Cache hit rate", `${((usage.data?.totals.cache_hit_rate ?? 0) * 100).toFixed(0)}%`],
          ].map(([label, value]) => (
            <div key={label as string}>
              <dt className="text-xs text-muted">{label}</dt>
              <dd className="text-lg font-semibold">{value}</dd>
            </div>
          ))}
        </dl>
      </Card>

      <Card title="Tokens by day">
        <div className="space-y-1 p-4">
          {byDay.length === 0 && <p className="text-sm text-muted">No calls recorded yet.</p>}
          {byDay.map(([day, value]) => {
            const total = value.prompt_tokens + value.completion_tokens;
            return (
              <div key={day} className="flex items-center gap-2 text-xs">
                <span className="w-20 shrink-0 text-muted">{day.slice(5)}</span>
                <div className="h-3 flex-1 overflow-hidden rounded bg-line/50">
                  <div
                    className="h-full rounded bg-accent"
                    style={{ width: `${(total / max) * 100}%` }}
                  />
                </div>
                <span className="w-16 shrink-0 text-right text-muted">{total.toLocaleString()}</span>
              </div>
            );
          })}
        </div>
      </Card>

      <Card title="By task">
        <ul className="divide-y divide-line text-sm">
          {Object.entries(usage.data?.by_task ?? {}).map(([task, value]) => (
            <li key={task} className="flex items-center justify-between gap-3 px-4 py-2">
              <span className="mono text-xs">{task}</span>
              <span className="text-xs text-muted">
                {value.calls} calls · {value.completion_tokens.toLocaleString()} out
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
