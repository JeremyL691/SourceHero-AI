"use client";

/** Jobs: what the pipeline is doing, and what it could not do. Schedules: what it runs on its own. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import type { Job } from "@/lib/types";
import { useToast } from "@/components/providers";
import { describeCadence, formatTimestamp, type Schedule } from "@/components/jobs/schedules";
import { Card, EmptyState, ErrorNotice, Skeleton, StatusDot, Tabs } from "@/components/ui";

const STATUSES = ["", "queued", "running", "failed", "dead", "succeeded"];
const TABS = [
  { id: "jobs", label: "Jobs" },
  { id: "schedules", label: "Schedules" },
];

export default function JobsPage() {
  const toast = useToast();
  const invalidate = useQueryClient();
  const [tab, setTab] = useState("jobs");
  const [status, setStatus] = useState("");

  const jobs = useQuery({
    queryKey: ["jobs", status],
    queryFn: () => api.get<Job[]>(`/jobs?limit=50${status ? `&status=${status}` : ""}`),
    refetchInterval: (query) => {
      const rows = query.state.data as Job[] | undefined;
      const busy = rows?.some((j) => j.status === "queued" || j.status === "running");
      return busy ? 2000 : false;
    },
  });

  const retry = useMutation({
    mutationFn: (id: number) => api.post(`/jobs/${id}/retry`),
    onSuccess: () => {
      toast("Requeued");
      invalidate.invalidateQueries();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Retry failed"),
  });
  const cancel = useMutation({
    mutationFn: (id: number) => api.post(`/jobs/${id}/cancel`),
    onSuccess: () => invalidate.invalidateQueries(),
    onError: (e) => toast(e instanceof Error ? e.message : "Cancel failed"),
  });

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="serif text-2xl font-semibold">Jobs</h1>
          <p className="text-sm text-muted">
            {tab === "jobs"
              ? `${jobs.data?.length ?? 0} recent jobs.`
              : "Recurring work the pipeline queues on its own."}
          </p>
        </div>
        {tab === "jobs" && (
          <label className="text-xs text-muted">
            Status
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className="ml-1 rounded border border-line bg-surface px-1.5 py-1"
            >
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s || "all"}
                </option>
              ))}
            </select>
          </label>
        )}
      </header>

      <Tabs active={tab} onChange={setTab} tabs={TABS} />

      {tab === "schedules" ? (
        <SchedulesPanel />
      ) : (
        <Card>
          {jobs.isLoading ? (
            <Skeleton rows={8} />
          ) : (jobs.data ?? []).length === 0 ? (
            <EmptyState title="No jobs" hint="Ingestion work will appear here as you capture sources." />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs tracking-wide text-muted uppercase">
                  <th className="px-4 py-2 font-medium">Kind</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Attempts</th>
                  <th className="px-4 py-2 font-medium">Finished</th>
                  <th className="px-4 py-2 font-medium">Error</th>
                  <th className="px-4 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {(jobs.data ?? []).map((job) => (
                  <tr key={job.id}>
                    <td className="mono px-4 py-2 text-xs">{job.kind}</td>
                    <td className="px-4 py-2">
                      <StatusDot status={job.status} />
                    </td>
                    <td className="px-4 py-2 text-muted">
                      {job.attempts}/{job.max_attempts}
                    </td>
                    <td className="px-4 py-2 text-xs text-muted">
                      {job.finished_at ? job.finished_at.slice(0, 19).replace("T", " ") : "—"}
                    </td>
                    <td className="max-w-xs truncate px-4 py-2 text-xs text-contradicted">
                      {job.last_error ?? ""}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex gap-2 text-xs">
                        {(job.status === "failed" || job.status === "dead") && (
                          <button
                            onClick={() => retry.mutate(job.id)}
                            className="text-accent underline underline-offset-2"
                          >
                            Retry
                          </button>
                        )}
                        {(job.status === "queued" || job.status === "running") && (
                          <button
                            onClick={() => cancel.mutate(job.id)}
                            className="text-muted underline underline-offset-2"
                          >
                            Cancel
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </div>
  );
}

function SchedulesPanel() {
  const schedules = useQuery({
    queryKey: ["schedules"],
    queryFn: () => api.get<Schedule[]>("/schedules"),
  });
  const rows = schedules.data ?? [];

  return (
    <Card>
      {schedules.isLoading ? (
        <Skeleton rows={5} />
      ) : schedules.error ? (
        <div className="p-4">
          <ErrorNotice error={schedules.error} onRetry={() => schedules.refetch()} />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          title="No schedules"
          hint="Open a source and set a schedule to refresh it on its own."
        />
      ) : (
        <table data-testid="k6-schedules-table" className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs tracking-wide text-muted uppercase">
              <th className="px-4 py-2 font-medium">Name</th>
              <th className="px-4 py-2 font-medium">Job</th>
              <th className="px-4 py-2 font-medium">Cadence</th>
              <th className="px-4 py-2 font-medium">State</th>
              <th className="px-4 py-2 font-medium">Next run</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((s) => (
              <tr key={s.id}>
                <td className="px-4 py-2">{s.name || "(unnamed)"}</td>
                <td className="mono px-4 py-2 text-xs">{s.job_type}</td>
                <td className="px-4 py-2 text-xs">{describeCadence(s)}</td>
                <td className="px-4 py-2">
                  <StatusDot status={s.enabled ? "active" : "paused"} />
                </td>
                <td data-testid="k6-schedule-next-run" className="px-4 py-2 text-xs text-muted">
                  {s.enabled ? formatTimestamp(s.next_run_at) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
