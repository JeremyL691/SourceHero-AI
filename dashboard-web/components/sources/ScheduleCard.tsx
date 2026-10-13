"use client";

/** Schedule card for one source: create, edit, pause and delete its refresh schedule. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import type { Job } from "@/lib/types";
import { useToast } from "@/components/providers";
import { WEEKDAYS, type Schedule, describeCadence, formatTimestamp } from "@/components/jobs/schedules";
import { Button, Card, ErrorNotice, Skeleton, StatusDot } from "@/components/ui";

type Cadence = { day: string; hour: string; minute: string };

const inputClass = "mt-1 block rounded-md border border-line bg-surface px-2 py-1 text-sm";

function isValid(c: Cadence): boolean {
  const h = Number(c.hour);
  const m = Number(c.minute);
  return (
    c.hour.trim() !== "" &&
    c.minute.trim() !== "" &&
    Number.isInteger(h) && h >= 0 && h <= 23 &&
    Number.isInteger(m) && m >= 0 && m <= 59
  );
}

/** An empty day means daily. Omitted fields are not sent, so the server keeps them. */
function toPayload(c: Cadence) {
  return {
    day_of_week: c.day === "" ? undefined : Number(c.day),
    hour: Number(c.hour),
    minute: Number(c.minute),
  };
}

export function ScheduleCard({ sourceId, sourceName }: { sourceId: string; sourceName: string }) {
  const toast = useToast();
  const invalidate = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [cadence, setCadence] = useState<Cadence>({ day: "", hour: "0", minute: "0" });

  const schedules = useQuery({
    queryKey: ["schedules"],
    queryFn: () => api.get<Schedule[]>("/schedules"),
  });
  const mine = (schedules.data ?? []).find(
    (s) => s.job_type === "refresh_source" && s.config.source_id === sourceId,
  );

  const runs = useQuery({
    queryKey: ["schedule-runs", mine?.id],
    queryFn: () => api.get<Job[]>(`/schedules/${mine?.id}/runs`),
    enabled: mine !== undefined,
  });

  // The API ignores null fields on PATCH, so a weekly schedule cannot go back to daily.
  const weeklyLocked = mine !== undefined && mine.cron_day_of_week != null;

  const refreshAll = () => {
    invalidate.invalidateQueries({ queryKey: ["schedules"] });
    invalidate.invalidateQueries({ queryKey: ["schedule-runs"] });
  };
  const onError = (e: unknown) => toast(e instanceof Error ? e.message : "Schedule request failed");

  const create = useMutation({
    mutationFn: () =>
      api.post<Schedule>("/schedules", {
        job_type: "refresh_source",
        name: `Refresh ${sourceName || sourceId}`.slice(0, 200),
        config: { source_id: sourceId },
        ...toPayload(cadence),
      }),
    onSuccess: () => {
      toast("Schedule created");
      setEditing(false);
      refreshAll();
    },
    onError,
  });
  const update = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patch<Schedule>(`/schedules/${mine?.id}`, body),
    onSuccess: () => {
      toast("Schedule saved");
      setEditing(false);
      refreshAll();
    },
    onError,
  });
  const remove = useMutation({
    mutationFn: () => api.delete(`/schedules/${mine?.id}`),
    onSuccess: () => {
      toast("Schedule deleted");
      setEditing(false);
      refreshAll();
    },
    onError,
  });

  const startEdit = () => {
    if (!mine) return;
    setCadence({
      day: mine.cron_day_of_week == null ? "" : String(mine.cron_day_of_week),
      hour: String(mine.cron_hour),
      minute: String(mine.cron_minute),
    });
    setEditing(true);
  };

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValid(cadence)) return;
    if (mine) update.mutate(toPayload(cadence));
    else create.mutate();
  };

  const busy = create.isPending || update.isPending || remove.isPending;
  const showForm = !mine || editing;

  return (
    <Card title="Schedule">
      <div data-testid="schedule-card" className="space-y-4 p-4">
        {schedules.isLoading ? (
          <Skeleton rows={2} />
        ) : schedules.error ? (
          <ErrorNotice error={schedules.error} onRetry={() => schedules.refetch()} />
        ) : !showForm && mine ? (
          <div className="space-y-4">
            <dl className="grid gap-3 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-xs text-muted">Cadence</dt>
                <dd data-testid="schedule-cadence">{describeCadence(mine)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted">Next run</dt>
                <dd data-testid="schedule-next-run">
                  {mine.enabled ? formatTimestamp(mine.next_run_at) : "Paused"}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted">State</dt>
                <dd>
                  <StatusDot status={mine.enabled ? "active" : "paused"} />
                </dd>
              </div>
            </dl>

            <div className="flex flex-wrap gap-2">
              <Button onClick={startEdit} disabled={busy}>
                Edit
              </Button>
              <Button onClick={() => update.mutate({ enabled: !mine.enabled })} disabled={busy}>
                {mine.enabled ? "Pause" : "Resume"}
              </Button>
              <Button
                variant="danger"
                onClick={() => {
                  if (window.confirm("Delete this schedule?")) remove.mutate();
                }}
                disabled={busy}
              >
                Delete
              </Button>
            </div>

            <div>
              <h3 className="mb-1 text-xs font-medium tracking-wide text-muted uppercase">Recent runs</h3>
              {runs.isLoading ? (
                <Skeleton rows={2} />
              ) : runs.error ? (
                <ErrorNotice error={runs.error} onRetry={() => runs.refetch()} />
              ) : (runs.data ?? []).length === 0 ? (
                <p className="text-sm text-muted">No runs yet.</p>
              ) : (
                <ul data-testid="schedule-runs" className="divide-y divide-line">
                  {(runs.data ?? []).slice(0, 5).map((run) => (
                    <li key={run.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                      <span>{formatTimestamp(run.run_after)}</span>
                      <StatusDot status={run.status} />
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ) : (
          <form onSubmit={save} aria-label="Schedule settings" className="space-y-3">
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-xs text-muted">
                Frequency
                <select
                  value={cadence.day}
                  onChange={(e) => setCadence({ ...cadence, day: e.target.value })}
                  className={inputClass}
                >
                  <option value="" disabled={weeklyLocked}>
                    Every day
                  </option>
                  {WEEKDAYS.map((name, i) => (
                    <option key={name} value={String(i)}>
                      Every {name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-muted">
                Hour
                <input
                  type="number"
                  min={0}
                  max={23}
                  value={cadence.hour}
                  onChange={(e) => setCadence({ ...cadence, hour: e.target.value })}
                  className={`${inputClass} w-20`}
                />
              </label>
              <label className="text-xs text-muted">
                Minute
                <input
                  type="number"
                  min={0}
                  max={59}
                  value={cadence.minute}
                  onChange={(e) => setCadence({ ...cadence, minute: e.target.value })}
                  className={`${inputClass} w-20`}
                />
              </label>
              <Button type="submit" variant="primary" disabled={!isValid(cadence) || busy}>
                {mine ? "Save changes" : "Create schedule"}
              </Button>
              {mine && (
                <Button type="button" onClick={() => setEditing(false)}>
                  Cancel
                </Button>
              )}
            </div>
            {weeklyLocked && (
              <p className="text-xs text-muted">
                A weekly schedule cannot return to daily. Delete it and create a new one to change this.
              </p>
            )}
          </form>
        )}
      </div>
    </Card>
  );
}
