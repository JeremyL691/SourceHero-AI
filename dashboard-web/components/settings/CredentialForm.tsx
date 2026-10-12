"use client";

/** Credentials card: pick one of API key, token command or OAuth, then store or connect. */

import { useMutation } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { Button, Card, StatusDot } from "@/components/ui";
import { Field } from "@/components/settings/Field";
import { OAuthConnect } from "@/components/settings/OAuthConnect";
import type { Invalidate, SettingsQuery, Toast } from "@/components/settings/types";

type Method = "api_key" | "command" | "oauth";

const METHODS: { id: Method; label: string }[] = [
  { id: "api_key", label: "API key" },
  { id: "command", label: "Token command" },
  { id: "oauth", label: "OAuth" },
];

export function CredentialForm({
  settings,
  toast,
  invalidate,
}: {
  settings: SettingsQuery;
  toast: Toast;
  invalidate: Invalidate;
}) {
  const [method, setMethod] = useState<Method>("api_key");
  const [key, setKey] = useState("");
  const [command, setCommand] = useState("");
  const [ttl, setTtl] = useState("");
  const data = settings.data;

  // Empty means "use the server default" (token_ttl_seconds from GET /settings/llm).
  const ttlNumber = ttl.trim() === "" ? undefined : Number(ttl);
  const ttlValid = ttlNumber === undefined || (Number.isInteger(ttlNumber) && ttlNumber > 0);

  const storeKey = useMutation({
    mutationFn: () =>
      api.post<unknown>("/settings/llm/credentials", { kind: "api_key", value: key }),
    onSuccess: () => {
      toast("API key stored (encrypted)");
      setKey("");
      invalidate.invalidateQueries({ queryKey: ["settings-llm"] });
      settings.refetch();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Could not store the key"),
  });

  const storeCommand = useMutation({
    mutationFn: () => {
      const ttlSeconds = ttlNumber ?? data?.token_ttl_seconds;
      return api.post<unknown>("/settings/llm/credentials", {
        kind: "command",
        value: command,
        ...(ttlSeconds === undefined ? {} : { ttl_seconds: ttlSeconds }),
      });
    },
    onSuccess: () => {
      toast("Token command stored");
      setCommand("");
      setTtl("");
      invalidate.invalidateQueries({ queryKey: ["settings-llm"] });
      settings.refetch();
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Could not store the command"),
  });

  return (
    <Card title="Credentials">
      <div role="group" aria-label="Authentication method" className="flex flex-wrap gap-2 p-4 pb-0">
        {METHODS.map((m) => (
          <button
            key={m.id}
            type="button"
            aria-pressed={method === m.id}
            data-testid={`k6-auth-${m.id}`}
            onClick={() => setMethod(m.id)}
            className={`rounded border px-2.5 py-1 text-sm ${
              method === m.id
                ? "border-accent bg-accent-soft text-accent"
                : "border-line text-muted hover:text-accent"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {method === "api_key" && (
        <div className="space-y-3 p-4">
          <p className="flex items-center gap-2 text-xs text-muted">
            <StatusDot status={data?.has_api_key ? "succeeded" : "paused"} />
            {data?.has_api_key
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
        </div>
      )}

      {method === "command" && (
        <div className="space-y-3 p-4">
          <textarea
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            rows={2}
            placeholder="Command that prints a token to stdout"
            aria-label="Token command"
            data-testid="k6-token-command"
            className="mono w-full rounded-md border border-line bg-surface px-2 py-1 text-xs"
          />
          <div className="flex flex-wrap items-end gap-2">
            <Field label="TTL (seconds)" hint="Optional. Defaults to the server value.">
              <input
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                value={ttl}
                onChange={(e) => setTtl(e.target.value)}
                placeholder={data?.token_ttl_seconds !== undefined ? String(data.token_ttl_seconds) : ""}
                aria-label="Token TTL (seconds)"
                data-testid="k6-token-ttl"
                className="w-32 rounded-md border border-line bg-surface px-2 py-1 text-sm"
              />
            </Field>
            <Button
              data-testid="k6-store-command"
              onClick={() => storeCommand.mutate()}
              disabled={storeCommand.isPending || !command.trim() || !ttlValid}
            >
              Store command
            </Button>
          </div>
          {!ttlValid && (
            <p className="text-xs text-contradicted">
              Enter a whole number of seconds greater than zero.
            </p>
          )}
        </div>
      )}

      {method === "oauth" && <OAuthConnect />}

      {data?.token_command_set && (
        <p className="px-4 pb-4 text-xs text-muted">A token command is also configured.</p>
      )}
    </Card>
  );
}
