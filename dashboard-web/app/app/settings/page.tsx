"use client";

/** Settings: the LLM, tokens and MCP snippets, usage, and the account. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Copy, KeyRound } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import type { ApiToken, LLMSettings, UsageReport, User } from "@/lib/types";
import { useToast } from "@/components/providers";
import { Button, Card, CodeBlock, Skeleton, StatusDot, Tabs } from "@/components/ui";

type Panel = "llm" | "tokens" | "usage" | "account";

const PRESET_NOTES: Record<string, string> = {
  "opencode-go":
    "Models are grouped by endpoint: chat/completions models use openai_chat, /messages models use anthropic_messages, and /responses models use openai_responses.",
  openai: "Choose the model with Fetch models after entering an API key.",
  fake: "Runs entirely offline on heuristic implementations. Nothing leaves this machine.",
};

export default function SettingsPage() {
  const toast = useToast();
  const invalidate = useQueryClient();
  const [panel, setPanel] = useState<Panel>("llm");

  const settings = useQuery({
    queryKey: ["llm-settings"],
    queryFn: () => api.get<LLMSettings>("/settings/llm"),
  });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/auth/me") });

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-muted">
          Everything here is local to this instance. Nothing is sent anywhere unless you
          configure a model and use it.
        </p>
      </header>

      <Tabs
        active={panel}
        onChange={(id) => setPanel(id as Panel)}
        tabs={[
          { id: "llm", label: "Model" },
          { id: "tokens", label: "Tokens & MCP" },
          { id: "usage", label: "Usage" },
          { id: "account", label: "Account" },
        ]}
      />

      {panel === "llm" && <LLMPanel settings={settings} me={me} toast={toast} invalidate={invalidate} />}
      {panel === "tokens" && <TokensPanel toast={toast} />}
      {panel === "usage" && <UsagePanel />}
      {panel === "account" && <AccountPanel me={me} />}
    </div>
  );
}

function LLMPanel({
  settings,
  me,
  toast,
  invalidate,
}: {
  settings: ReturnType<typeof useQuery<LLMSettings>>;
  me: ReturnType<typeof useQuery<User>>;
  toast: (m: string) => void;
  invalidate: ReturnType<typeof useQueryClient>;
}) {
  const [form, setForm] = useState<Partial<LLMSettings>>({});
  const [key, setKey] = useState("");
  const [oauthError, setOauthError] = useState<string | null>(null);
  const [connectingOAuth, setConnectingOAuth] = useState(false);
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

  const storeKey = useMutation({
    mutationFn: () =>
      api.post<LLMSettings>("/settings/llm/credentials", { kind: "api_key", value: key }),
    onSuccess: () => {
      toast("API key stored (encrypted)");
      setKey("");
      invalidate.invalidateQueries({ queryKey: ["settings-llm"] });
      settings.refetch();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Could not store the key"),
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

  if (me.data && !me.data.is_admin) {
    return (
      <Card title="Model">
        <div className="p-4 text-sm text-muted">
          Administrator privileges are required to view or configure instance model settings.
        </div>
      </Card>
    );
  }

  if (settings.isLoading) return <Skeleton rows={8} />;

  return (
    <div className="space-y-4">
      <Card title="Provider">
        <div className="space-y-3 p-4">
          <div className="flex flex-wrap gap-2">
            {(settings.data?.presets ?? ["fake", "opencode-go", "openai"]).map((preset) => (
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
          {current.preset && PRESET_NOTES[current.preset] && (
            <p className="text-xs text-muted">{PRESET_NOTES[current.preset]}</p>
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
                className="w-full rounded-md border border-line bg-surface px-2 py-1 text-sm"
              />
            </Field>
            <Field label="Smart model" hint="Harder tasks: judging, writing.">
              <input
                value={current.model_smart ?? ""}
                onChange={(e) => setForm({ ...form, model_smart: e.target.value })}
                className="w-full rounded-md border border-line bg-surface px-2 py-1 text-sm"
              />
            </Field>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending || !Object.keys(form).length}>
              Save
            </Button>
            <Button onClick={() => test.mutate()} disabled={test.isPending}>
              {test.isPending ? "Testing…" : "Test connection"}
            </Button>
          </div>
        </div>
      </Card>

      <Card title="Credentials">
        <div className="space-y-3 p-4">
          <p className="flex items-center gap-2 text-xs text-muted">
            <StatusDot status={settings.data?.has_api_key ? "succeeded" : "paused"} />
            {settings.data?.has_api_key
              ? "An API key is stored (encrypted at rest)."
              : "No API key stored. The app works without one, on its heuristics."}
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="sk-…"
              aria-label="API key"
              className="flex-1 rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
            <Button
              onClick={() => storeKey.mutate()}
              disabled={storeKey.isPending || !key.trim()}
            >
              <KeyRound className="h-4 w-4" aria-hidden />
              Store key
            </Button>
          </div>
          {settings.data?.token_command_set && (
            <p className="text-xs text-muted">A token command is also configured.</p>
          )}
        </div>
      </Card>

      <Card title="OAuth">
        <div className="space-y-3 p-4 text-sm">
          <p className="text-muted">
            Connect through OAuth by setting the authorize and token endpoints plus a
            client id in <code className="mono text-xs">.env</code>, then refreshing this
            page.
          </p>
          {oauthError && (
            <p
              role="alert"
              className="rounded-md border border-contradicted/40 bg-contradicted-soft px-3 py-2 text-xs text-contradicted"
            >
              {oauthError}
            </p>
          )}
          <Button
            onClick={async () => {
              setOauthError(null);
              setConnectingOAuth(true);
              try {
                const res = await api.get<{ authorize_url: string }>("/settings/llm/oauth/start");
                if (res.authorize_url) {
                  window.location.href = res.authorize_url;
                }
              } catch (e) {
                if (e instanceof ApiError && e.status === 409) {
                  const details = e.details as { missing?: string[] } | undefined;
                  const missing = details?.missing?.length
                    ? ` (Missing: ${details.missing.join(", ")})`
                    : "";
                  setOauthError(`${e.message}${missing}`);
                } else {
                  setOauthError(e instanceof Error ? e.message : "Failed to start OAuth");
                }
              } finally {
                setConnectingOAuth(false);
              }
            }}
            disabled={connectingOAuth}
          >
            {connectingOAuth ? "Connecting…" : "Connect with OAuth"}
          </Button>
        </div>
      </Card>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-xs text-muted">
      {label}
      {hint && <span className="block text-[11px] opacity-70">{hint}</span>}
      <span className="mt-1 block">{children}</span>
    </label>
  );
}

function TokensPanel({ toast }: { toast: (m: string) => void }) {
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: () => api.get<ApiToken[]>("/tokens") });
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiToken | null>(null);

  const create = useMutation({
    mutationFn: () => api.post<ApiToken>("/tokens", { name }),
    onSuccess: (token) => {
      setCreated(token);
      setName("");
      tokens.refetch();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Could not create the token"),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api.delete(`/tokens/${id}`),
    onSuccess: () => tokens.refetch(),
    onError: (e) => toast(e instanceof Error ? e.message : "Could not revoke"),
  });

  const port = typeof window !== "undefined" ? window.location.hostname : "127.0.0.1";
  const apiPort = process.env.NEXT_PUBLIC_API_URL?.split(":").pop() ?? "8787";
  const base = `http://${port}:${apiPort}`;
  const snippet = created?.token
    ? `# Claude Code
claude mcp add --transport http sourcehero ${base}/mcp --header "Authorization: Bearer ${created.token}"

# opencode.json
{"mcp":{"sourcehero":{"type":"remote","url":"${base}/mcp","headers":{"Authorization":"Bearer ${created.token}"}}}}

# Codex (~/.codex/config.toml)
[mcp_servers.sourcehero]
command = "uv"
args = ["run", "--directory", "/path/to/SourceHero-AI", "python", "-m", "app.mcp"]
env = { SOURCEHERO_TOKEN = "${created.token}", DATABASE_URL = "postgresql+psycopg://…" }`
    : `# Create a token above to see the configuration snippets.`;

  return (
    <div className="space-y-4">
      <Card title="Tokens">
        <div className="space-y-3 p-4">
          {created?.token && (
            <div className="rounded-md border border-disputed/40 bg-disputed-soft p-3 text-sm text-disputed">
              <p className="font-medium">Copy this now — it is shown only once.</p>
              <p className="mono mt-1 text-xs break-all">{created.token}</p>
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="What is this token for?"
              aria-label="Token name"
              className="flex-1 rounded-md border border-line bg-surface px-2 py-1 text-sm"
            />
            <Button variant="primary" onClick={() => create.mutate()} disabled={!name.trim() || create.isPending}>
              {create.isPending ? "Creating…" : "Create token"}
            </Button>
          </div>
          <ul className="divide-y divide-line">
            {(tokens.data ?? []).map((token) => (
              <li key={token.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span>
                  {token.name} <span className="mono text-xs text-muted">{token.prefix}…</span>
                  {token.revoked_at && <span className="ml-2 text-xs text-muted">revoked</span>}
                </span>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted">{token.scopes.join(", ")}</span>
                  {!token.revoked_at && (
                    <button
                      onClick={() => revoke.mutate(token.id)}
                      className="text-xs text-contradicted underline underline-offset-2"
                    >
                      Revoke
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      </Card>

      <Card title="MCP configuration">
        <div className="space-y-2 p-4">
          <CodeBlock>{snippet}</CodeBlock>
          <p className="flex items-center gap-1 text-xs text-muted">
            <Copy className="h-3 w-3" aria-hidden />
            The endpoint is /mcp. A token is required; a session JWT is not accepted.
          </p>
        </div>
      </Card>
    </div>
  );
}

function UsagePanel() {
  const usage = useQuery({ queryKey: ["usage"], queryFn: () => api.get<UsageReport>("/usage?days=7") });
  if (usage.isLoading) return <Skeleton rows={6} />;

  const byDay = Object.entries(usage.data?.by_day ?? {});
  const max = Math.max(1, ...byDay.map(([, v]) => v.completion_tokens + v.prompt_tokens));

  return (
    <div className="space-y-4">
      <Card title="Totals">
        <dl className="grid grid-cols-2 gap-3 p-4 text-sm sm:grid-cols-4">
          {[
            ["Calls", usage.data?.totals.calls ?? 0],
            ["Prompt tokens", usage.data?.totals.prompt_tokens ?? 0],
            ["Completion tokens", usage.data?.totals.completion_tokens ?? 0],
            ["Cache hit rate", `${((usage.data?.totals.cache_hit_rate ?? 0) * 100).toFixed(0)}%`],
          ].map(([label, value]) => (
            <div key={label as string}>
              <dt className="text-xs text-muted">{label}</dt>
              <dd className="text-lg font-semibold">{value}</dd>
            </div>
          ))}
        </dl>
      </Card>

      <Card title="Tokens by day">
        <div className="space-y-1 p-4">
          {byDay.length === 0 && <p className="text-sm text-muted">No calls recorded yet.</p>}
          {byDay.map(([day, value]) => {
            const total = value.prompt_tokens + value.completion_tokens;
            return (
              <div key={day} className="flex items-center gap-2 text-xs">
                <span className="w-20 shrink-0 text-muted">{day.slice(5)}</span>
                <div className="h-3 flex-1 overflow-hidden rounded bg-line/50">
                  <div
                    className="h-full rounded bg-accent"
                    style={{ width: `${(total / max) * 100}%` }}
                  />
                </div>
                <span className="w-16 shrink-0 text-right text-muted">{total.toLocaleString()}</span>
              </div>
            );
          })}
        </div>
      </Card>

      <Card title="By task">
        <ul className="divide-y divide-line text-sm">
          {Object.entries(usage.data?.by_task ?? {}).map(([task, value]) => (
            <li key={task} className="flex items-center justify-between gap-3 px-4 py-2">
              <span className="mono text-xs">{task}</span>
              <span className="text-xs text-muted">
                {value.calls} calls · {value.completion_tokens.toLocaleString()} out
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function AccountPanel({ me }: { me: ReturnType<typeof useQuery<User>> }) {
  return (
    <Card title="Account">
      <dl className="space-y-2 p-4 text-sm">
        <div>
          <dt className="text-xs text-muted">Email</dt>
          <dd>{me.data?.email ?? "…"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Role</dt>
          <dd>{me.data?.is_admin ? "Administrator" : "Member"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Sign-in method</dt>
          <dd className="mono text-xs">{me.data?.auth_provider ?? "…"}</dd>
        </div>
      </dl>
    </Card>
  );
}
