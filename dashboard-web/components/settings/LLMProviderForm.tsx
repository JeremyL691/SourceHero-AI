"use client";

/** Provider card: presets, endpoint fields, model fields with Fetch models, save and test. */

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import type { LLMSettings } from "@/lib/types";
import { Button, Card } from "@/components/ui";
import { Field } from "@/components/settings/Field";
import type { Invalidate, SettingsQuery, Toast } from "@/components/settings/types";

const PRESET_NOTES: Record<string, string> = {
  "opencode-go":
    "Models are grouped by endpoint: chat/completions models use openai_chat, /messages models use anthropic_messages, and /responses models use openai_responses.",
  openai: "Choose the model with Fetch models after entering an API key.",
  fake: "Runs entirely offline on heuristic implementations. Nothing leaves this machine.",
  openrouter: "Free models only (:free suffix or openrouter/free).",
};

const FALLBACK_PRESETS = ["fake", "opencode-go", "openai", "openrouter"];

export function LLMProviderForm({
  settings,
  toast,
  invalidate,
}: {
  settings: SettingsQuery;
  toast: Toast;
  invalidate: Invalidate;
}) {
  const [form, setForm] = useState<Partial<LLMSettings>>({});
  const [models, setModels] = useState<string[]>([]);
  const current = { ...(settings.data ?? {}), ...form };

  const save = useMutation({
    mutationFn: () => api.put<LLMSettings>("/settings/llm", form),
    onSuccess: () => {
      toast("Saved");
      setForm({});
      invalidate.invalidateQueries({ queryKey: ["settings-llm"] });
      settings.refetch();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Could not save"),
  });

  const test = useMutation({
    mutationFn: () =>
      api.post<{ provider: string; mode: string; tiers: Record<string, unknown> }>(
        "/settings/llm/test",
      ),
    onSuccess: (r) => {
      const fast = r.tiers?.fast as { ok?: boolean; error?: string } | undefined;
      toast(fast?.ok ? `Connected on ${r.provider}` : `Failed: ${fast?.error ?? "unknown"}`);
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Test failed"),
  });

  // Fetches from the saved settings on the server, so unsaved edits are not used here.
  const fetchModels = useMutation({
    mutationFn: () => api.get<{ models: string[] }>("/settings/llm/models"),
    onSuccess: (r) => {
      const list = r.models ?? [];
      setModels(list);
      toast(list.length ? `Found ${list.length} models` : "No models returned");
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Could not fetch models"),
  });

  const note = current.preset ? PRESET_NOTES[current.preset] : undefined;

  return (
    <Card title="Provider">
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap gap-2">
          {(settings.data?.presets ?? FALLBACK_PRESETS).map((preset) => (
            <button
              key={preset}
              onClick={() => setForm({ ...form, preset })}
              className={`rounded border px-2.5 py-1 text-sm ${
                current.preset === preset
                  ? "border-accent bg-accent-soft text-accent"
                  : "border-line text-muted hover:text-accent"
              }`}
            >
              {preset}
            </button>
          ))}
        </div>
        {note && (
          <p data-testid="k6-preset-note" className="text-xs text-muted">
            {note}
          </p>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Provider" hint="fake | openai_chat | openai_responses | anthropic_messages">
            <input
              value={current.provider ?? ""}
              onChange={(e) => setForm({ ...form, provider: e.target.value })}
              className="w-full rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
          </Field>
          <Field label="Base URL">
            <input
              value={current.base_url ?? ""}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              className="w-full rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
          </Field>
          <Field label="Fast model" hint="Cheap tasks: extraction, labels.">
            <input
              value={current.model_fast ?? ""}
              onChange={(e) => setForm({ ...form, model_fast: e.target.value })}
              list="k6-model-options"
              className="w-full rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
          </Field>
          <Field label="Smart model" hint="Harder tasks: judging, writing.">
            <input
              value={current.model_smart ?? ""}
              onChange={(e) => setForm({ ...form, model_smart: e.target.value })}
              list="k6-model-options"
              className="w-full rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
          </Field>
        </div>
        <datalist id="k6-model-options" data-testid="k6-model-options">
          {models.map((model) => (
            <option key={model} value={model} />
          ))}
        </datalist>

        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending || !Object.keys(form).length}>
            Save
          </Button>
          <Button onClick={() => test.mutate()} disabled={test.isPending}>
            {test.isPending ? "Testing…" : "Test connection"}
          </Button>
          <Button
            data-testid="k6-fetch-models"
            onClick={() => fetchModels.mutate()}
            disabled={fetchModels.isPending}
          >
            {fetchModels.isPending ? "Fetching…" : "Fetch models"}
          </Button>
        </div>
      </div>
    </Card>
  );
}
