"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";

interface Schedule {
  id: number;
  job_type: string;
  name: string;
  status: string;
  schedule_kind: string;
  time_local: string;
  next_run_at: string;
  last_error: string | null;
}

export default function SchedulesPage() {
  const { user } = useAuth();
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchSchedules();
  }, [user]);

  const fetchSchedules = async () => {
    if (!user) return;
    try {
      const token = (await user.getSession()).access_token;
      const data = await api<Schedule[]>("/schedules", { token });
      setSchedules(data);
    } catch (err) {
      console.error("Failed to fetch schedules:", err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="text-center py-8">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Schedules</h1>

      <div className="bg-white rounded-lg border border-gray-200">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="text-left px-6 py-3 text-sm font-medium text-gray-500">Name</th>
              <th className="text-left px-6 py-3 text-sm font-medium text-gray-500">Type</th>
              <th className="text-left px-6 py-3 text-sm font-medium text-gray-500">Schedule</th>
              <th className="text-left px-6 py-3 text-sm font-medium text-gray-500">Next Run</th>
              <th className="text-left px-6 py-3 text-sm font-medium text-gray-500">Status</th>
            </tr>
          </thead>
          <tbody>
            {schedules.map((schedule) => (
              <tr key={schedule.id} className="border-b border-gray-100">
                <td className="px-6 py-4">{schedule.name}</td>
                <td className="px-6 py-4">{schedule.job_type}</td>
                <td className="px-6 py-4">
                  {schedule.schedule_kind} at {schedule.time_local}
                </td>
                <td className="px-6 py-4 text-sm text-gray-500">
                  {new Date(schedule.next_run_at).toLocaleString()}
                </td>
                <td className="px-6 py-4">
                  <span
                    className={`px-2 py-1 text-xs rounded-full ${
                      schedule.status === "active"
                        ? "bg-green-100 text-green-800"
                        : "bg-yellow-100 text-yellow-800"
                    }`}
                  >
                    {schedule.status}
                  </span>
                </td>
              </tr>
            ))}
            {schedules.length === 0 && (
              <tr>
                <td colSpan={5} className="px-6 py-8 text-center text-gray-500">
                  No scheduled jobs yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
