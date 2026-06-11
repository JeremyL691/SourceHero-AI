"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";

interface Document {
  id: number;
  source_id: number;
  title: string;
  url: string | null;
  source_name: string | null;
  source_type: string | null;
  fetched_at: string;
}

export default function LibraryPage() {
  const { user } = useAuth();
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchDocuments();
  }, [user]);

  const fetchDocuments = async () => {
    if (!user) return;
    try {
      const token = (await user.getSession()).access_token;
      const data = await api<Document[]>("/documents", { token });
      setDocuments(data);
    } catch (err) {
      console.error("Failed to fetch documents:", err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="text-center py-8">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Library</h1>

      <div className="bg-white rounded-lg border border-gray-200">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="text-left px-6 py-3 text-sm font-medium text-gray-500">Title</th>
              <th className="text-left px-6 py-3 text-sm font-medium text-gray-500">Source</th>
              <th className="text-left px-6 py-3 text-sm font-medium text-gray-500">Type</th>
              <th className="text-left px-6 py-3 text-sm font-medium text-gray-500">Added</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => (
              <tr key={doc.id} className="border-b border-gray-100">
                <td className="px-6 py-4">
                  {doc.url ? (
                    <a href={doc.url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                      {doc.title}
                    </a>
                  ) : (
                    doc.title
                  )}
                </td>
                <td className="px-6 py-4 text-gray-600">{doc.source_name}</td>
                <td className="px-6 py-4 text-gray-600">{doc.source_type}</td>
                <td className="px-6 py-4 text-gray-500 text-sm">
                  {new Date(doc.fetched_at).toLocaleDateString()}
                </td>
              </tr>
            ))}
            {documents.length === 0 && (
              <tr>
                <td colSpan={4} className="px-6 py-8 text-center text-gray-500">
                  No documents yet. Add and ingest sources to populate your library.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
