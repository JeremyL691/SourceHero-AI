"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";

interface Settings {
  openai_configured: boolean;
  openai_key_preview: string | null;
  openai_model: string;
}

export default function SettingsPage() {
  const { user } = useAuth();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [model, setModel] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchSettings();
  }, [user]);

  const fetchSettings = async () => {
    if (!user) return;
    try {
      const token = (await user.getSession()).access_token;
      const data = await api<Settings>("/settings", { token });
      setSettings(data);
      setModel(data.openai_model);
    } catch (err) {
      console.error("Failed to fetch settings:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user) return;

    setSaving(true);
    try {
      const token = (await user.getSession()).access_token;
      await api("/settings", {
        method: "POST",
        token,
        body: { openai_model: model },
      });
      fetchSettings();
    } catch (err) {
      console.error("Failed to save settings:", err);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="text-center py-8">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-900">Settings</h1>

      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">OpenAI Configuration</h2>
        <div className="space-y-4">
          <div>
            <div className="text-sm text-gray-500">API Key Status</div>
            <div className="mt-1">
              {settings?.openai_configured ? (
                <span className="text-green-600">
                  Configured ({settings.openai_key_preview})
                </span>
              ) : (
                <span className="text-gray-500">Not configured</span>
              )}
            </div>
          </div>
          <form onSubmit={handleSave} className="flex gap-2">
            <input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="Model (e.g., gpt-5.4-mini)"
              className="flex-1 px-4 py-2 border border-gray-300 rounded-lg"
            />
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </form>
        </div>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Account</h2>
        <div className="text-sm text-gray-600">
          <p>Email: {user?.email}</p>
        </div>
      </div>
    </div>
  );
}
