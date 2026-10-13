"use client";

/** OAuth entry point: starts the PKCE flow on the server and follows the authorize URL. */

import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import { Button } from "@/components/ui";

export function OAuthConnect() {
  const [oauthError, setOauthError] = useState<string | null>(null);
  const [connectingOAuth, setConnectingOAuth] = useState(false);

  return (
    <div className="space-y-3 p-4 text-sm">
      <p className="text-muted">
        Connect through OAuth by setting the authorize and token endpoints plus a client id in{" "}
        <code className="mono text-xs">.env</code>, then refreshing this page.
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
  );
}
