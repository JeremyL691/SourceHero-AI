"use client";

/** Account tab: the signed-in user's email, role and sign-in method. */

import { Card } from "@/components/ui";
import type { MeQuery } from "@/components/settings/types";

export function AccountPanel({ me }: { me: MeQuery }) {
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
