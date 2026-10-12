"use client";

/** The caller's share links, without tokens, each with a Revoke action. */

import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button, Card, EmptyState, ErrorNotice, Skeleton } from "@/components/ui";

type ShareOut = {
  id: number;
  kind: string;
  target_id: string;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
};

function describe(share: ShareOut): string {
  if (share.revoked_at) return "Revoked";
  if (share.expires_at && new Date(share.expires_at).getTime() <= Date.now()) return "Expired";
  if (share.expires_at) return `Expires ${new Date(share.expires_at).toLocaleDateString()}`;
  return "No expiry";
}

export function ManageShares() {
  const queryClient = useQueryClient();
  const shares = useQuery({
    queryKey: ["shares"],
    queryFn: () => api.get<ShareOut[]>("/shares"),
  });

  const revoke = useMutation({
    mutationFn: (id: number) => api.delete<void>(`/shares/${id}`),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["shares"] }),
  });

  return (
    <Card>
      <div data-testid="k8-manage-shares" className="flex flex-col gap-3 p-4">
        <h2 className="text-sm font-semibold text-ink">Shares</h2>
        {shares.isLoading && <Skeleton rows={2} />}
        {shares.error && <ErrorNotice error={shares.error} onRetry={() => shares.refetch()} />}
        {revoke.error && <ErrorNotice error={revoke.error} />}
        {shares.data && shares.data.length === 0 && (
          <EmptyState title="No share links yet" hint="Use Share on a page or an answer to create one." />
        )}
        <ul className="divide-y divide-line">
          {(shares.data ?? []).map((share) => {
            const live = !share.revoked_at;
            return (
              <li
                key={share.id}
                data-testid={`k8-share-row-${share.id}`}
                className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm"
              >
                <span className="text-ink">
                  {share.kind === "wiki_page" ? "Wiki page" : "Answer"} · {share.target_id}
                </span>
                <span className="flex items-center gap-3 text-muted">
                  <span>{describe(share)}</span>
                  {live && (
                    <Button
                      variant="danger"
                      data-testid={`k8-revoke-${share.id}`}
                      disabled={revoke.isPending}
                      onClick={() => revoke.mutate(share.id)}
                    >
                      Revoke
                    </Button>
                  )}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </Card>
  );
}
