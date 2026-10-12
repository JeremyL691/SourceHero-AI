"use client";

/** Tokens & MCP tab: create and revoke API tokens, and show the MCP client snippets. */

import { useMutation, useQuery } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import type { ApiToken } from "@/lib/types";
import { Button, Card, CodeBlock } from "@/components/ui";
import type { Toast } from "@/components/settings/types";

export function TokensPanel({ toast }: { toast: Toast }) {
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

  // The API URL may be absolute (http://host:8787) or relative behind the reverse proxy (/api).
  // Resolve it against the page so both work; the MCP snippets use the resolved base.
  const apiRaw = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8787";
  const base = (typeof window !== "undefined" ? new URL(apiRaw, window.location.href).toString() : apiRaw).replace(/\/$/, "");
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
