"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";

interface Briefing {
  id: number;
  query: string;
  answer_markdown: string;
  created_at: string;
}

export default function BriefingsPage() {
  const { user, getAccessToken } = useAuth();
  const [briefings, setBriefings] = useState<Briefing[]>([]);
  const [loading, setLoading] = useState(true);
  const [topic, setTopic] = useState("");
  const [generating, setGenerating] = useState(false);

  const fetchBriefings = async () => {
    if (!user) return;
    try {
      const token = await getAccessToken();
      const data = await api<Briefing[]>("/briefings", { token });
      setBriefings(data);
    } catch (err) {
      console.error("Failed to fetch briefings:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchBriefings();
  }, [user]);



  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user || !topic.trim()) return;

    setGenerating(true);
    try {
      const token = await getAccessToken();
      await api("/briefings", {
        method: "POST",
        token,
        body: { topic: topic.trim(), top_k: 8 },
      });
      setTopic("");
      fetchBriefings();
    } catch (err) {
      console.error("Failed to generate briefing:", err);
    } finally {
      setGenerating(false);
    }
  };

  if (loading) {
    return <div className="text-center py-8">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Briefings</h1>

      <form onSubmit={handleGenerate} className="flex gap-2">
        <input
          type="text"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="Enter a topic for your briefing..."
          className="flex-1 px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          type="submit"
          disabled={generating || !topic.trim()}
          className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          {generating ? "Generating..." : "Generate"}
        </button>
      </form>

      <div className="space-y-4">
        {briefings.map((briefing) => (
          <div key={briefing.id} className="bg-white rounded-lg border border-gray-200 p-6">
            <div className="flex justify-between items-start mb-2">
              <h2 className="text-lg font-semibold text-gray-900">{briefing.query}</h2>
              <span className="text-sm text-gray-500">
                {new Date(briefing.created_at).toLocaleDateString()}
              </span>
            </div>
            <div className="prose max-w-none whitespace-pre-wrap text-gray-600">
              {briefing.answer_markdown}
            </div>
          </div>
        ))}
        {briefings.length === 0 && (
          <div className="bg-white rounded-lg border border-gray-200 p-8 text-center text-gray-500">
            No briefings yet. Enter a topic above to generate your first briefing.
          </div>
        )}
      </div>
    </div>
  );
}
