/** Shared helpers for schedules, used by the source page and the jobs page. */

import type { components } from "@/lib/api-types";

export type Schedule = components["schemas"]["ScheduleOut"];

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const pad = (n: number) => String(n).padStart(2, "0");

/** Human cadence, e.g. "Every Monday at 06:30". The API carries no timezone, so none is shown. */
export function describeCadence(s: Pick<Schedule, "cron_day_of_week" | "cron_hour" | "cron_minute">): string {
  const day = s.cron_day_of_week == null ? "Every day" : `Every ${WEEKDAYS[s.cron_day_of_week]}`;
  return `${day} at ${pad(s.cron_hour)}:${pad(s.cron_minute)}`;
}

export function formatTimestamp(value: string | null | undefined): string {
  return value ? value.slice(0, 16).replace("T", " ") : "—";
}
