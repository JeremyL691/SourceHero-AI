"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";

interface SearchHit {
  chunk_id: number;
  document_id: number;
  source_id: number;
  title: string;
  source_name: string;
  source_type: string;
  url: string | null;
  score: number;
  snippet: string;
  citation: string;
  metadata: Record<string, unknown>;
}

interface SearchResponse {
  query: string;
  answer_markdown: string;
  effective_retrieval_mode: string;
  hits: SearchHit[];
}

export default function AskPage() {
  const { user, getAccessToken } = useAuth();
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user || !query.trim()) return;

    setLoading(true);
    try {
      const token = await getAccessToken();
      const data = await api<SearchResponse>("/search", {
        method: "POST",
        token,
        body: {
          query: query.trim(),
          top_k: 5,
          retrieval_mode: "hybrid",
        },
      });
      setResult(data);
    } catch (err) {
      console.error("Search failed:", err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Ask</h1>

      <form onSubmit={handleSearch} className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask a question about your sources..."
          className="flex-1 px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          type="submit"
          disabled={loading || !query.trim()}
          className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? "Searching..." : "Search"}
        </button>
      </form>

      {result && (
        <div className="space-y-6">
          <div className="bg-white rounded-lg border border-gray-200 p-6">
            <div className="text-sm text-gray-500 mb-2">
              Retrieval mode: {result.effective_retrieval_mode}
            </div>
            <div className="prose max-w-none whitespace-pre-wrap">{result.answer_markdown}</div>
          </div>

          {result.hits.length > 0 && (
            <div>
              <h2 className="text-lg font-semibold text-gray-900 mb-4">Sources</h2>
              <div className="space-y-3">
                {result.hits.map((hit) => (
                  <div
                    key={hit.chunk_id}
                    className="bg-white rounded-lg border border-gray-200 p-4"
                  >
                    <div className="flex justify-between items-start">
                      <div>
                        <div className="font-medium text-gray-900">{hit.title}</div>
                        <div className="text-sm text-gray-500">{hit.source_name}</div>
                      </div>
                      <div className="text-sm text-gray-500">
                        Score: {(hit.score * 100).toFixed(1)}%
                      </div>
                    </div>
                    <p className="mt-2 text-gray-600 text-sm">{hit.snippet}</p>
                    {hit.url && (
                      <a
                        href={hit.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="mt-2 inline-block text-sm text-blue-600 hover:underline"
                      >
                        View source
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
