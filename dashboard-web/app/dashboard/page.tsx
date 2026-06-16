"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";

interface Stats {
  sources: number;
  documents: number;
  chunks: number;
  ingestion_runs: number;
}

export default function DashboardPage() {
  const { user, getAccessToken } = useAuth();
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchStats = async () => {
      if (!user) return;
      try {
        const token = await getAccessToken();
        const data = await api<Stats>("/stats", { token });
        setStats(data);
      } catch (err) {
        console.error("Failed to fetch stats:", err);
      } finally {
        setLoading(false);
      }
    };

    fetchStats();
  }, [user]);

  if (loading) {
    return <div className="text-center py-8">Loading...</div>;
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-600">Welcome to your knowledge base</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <div className="text-sm text-gray-500">Sources</div>
          <div className="text-3xl font-bold text-gray-900 mt-1">{stats?.sources || 0}</div>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <div className="text-sm text-gray-500">Documents</div>
          <div className="text-3xl font-bold text-gray-900 mt-1">{stats?.documents || 0}</div>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <div className="text-sm text-gray-500">Chunks</div>
          <div className="text-3xl font-bold text-gray-900 mt-1">{stats?.chunks || 0}</div>
        </div>
        <div className="bg-white rounded-lg border border-gray-200 p-6">
          <div className="text-sm text-gray-500">Ingestion Runs</div>
          <div className="text-3xl font-bold text-gray-900 mt-1">{stats?.ingestion_runs || 0}</div>
        </div>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Quick Start</h2>
        <div className="space-y-4">
          <p className="text-gray-600">Get started with SourceHero AI:</p>
          <ol className="list-decimal list-inside space-y-2 text-gray-600">
            <li>Add your first source (RSS feed, webpage, or PDF)</li>
            <li>Wait for ingestion to complete</li>
            <li>Ask questions about your content</li>
            <li>Generate briefings from your sources</li>
          </ol>
        </div>
      </div>
    </div>
  );
}
