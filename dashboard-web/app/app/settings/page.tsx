"use client";

/** Settings: the LLM, tokens and MCP snippets, usage, and the account. */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { LLMSettings, UsageReport, User } from "@/lib/types";
import { useToast } from "@/components/providers";
import { Tabs } from "@/components/ui";
import { AccountPanel } from "@/components/settings/AccountPanel";
import { LLMPanel } from "@/components/settings/LLMPanel";
import { TokensPanel } from "@/components/settings/TokensPanel";
import { BookmarkletCard } from "@/components/settings/BookmarkletCard";
import { UsagePanel } from "@/components/settings/UsagePanel";

type Panel = "llm" | "tokens" | "usage" | "account";

export default function SettingsPage() {
  const toast = useToast();
  const invalidate = useQueryClient();
  const [panel, setPanel] = useState<Panel>("llm");

  const settings = useQuery({
    queryKey: ["llm-settings"],
    queryFn: () => api.get<LLMSettings>("/settings/llm"),
  });
  const me = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/auth/me") });
  // Usage is admin-only. A 403 hides the Usage tab rather than breaking the page.
  const usage = useQuery({
    queryKey: ["usage"],
    queryFn: () => api.get<UsageReport>("/usage?days=7"),
    retry: false,
  });
  const usageHidden = usage.error instanceof ApiError && usage.error.status === 403;

  const tabs = [
    { id: "llm", label: "Model" },
    { id: "tokens", label: "Tokens & MCP" },
    ...(usageHidden ? [] : [{ id: "usage", label: "Usage" }]),
    { id: "account", label: "Account" },
  ];

  return (
    <div className="space-y-6">
      <header>
        <h1 className="serif text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-muted">
          Everything here is local to this instance. Nothing is sent anywhere unless you
          configure a model and use it.
        </p>
      </header>

      <Tabs active={panel} onChange={(id) => setPanel(id as Panel)} tabs={tabs} />

      {panel === "llm" && <LLMPanel settings={settings} me={me} toast={toast} invalidate={invalidate} />}
      {panel === "tokens" && <TokensPanel toast={toast} />}
      {panel === "tokens" && <BookmarkletCard />}
      {panel === "usage" && <UsagePanel usage={usage} />}
      {panel === "account" && <AccountPanel me={me} />}
    </div>
  );
}
