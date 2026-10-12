"use client";

/** The Model tab: admin gate, then the provider form and the credentials card. */

import { Card, Skeleton } from "@/components/ui";
import { CredentialForm } from "@/components/settings/CredentialForm";
import { LLMProviderForm } from "@/components/settings/LLMProviderForm";
import type { Invalidate, MeQuery, SettingsQuery, Toast } from "@/components/settings/types";

export function LLMPanel({
  settings,
  me,
  toast,
  invalidate,
}: {
  settings: SettingsQuery;
  me: MeQuery;
  toast: Toast;
  invalidate: Invalidate;
}) {
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
      <LLMProviderForm settings={settings} toast={toast} invalidate={invalidate} />
      <CredentialForm settings={settings} toast={toast} invalidate={invalidate} />
    </div>
  );
}
