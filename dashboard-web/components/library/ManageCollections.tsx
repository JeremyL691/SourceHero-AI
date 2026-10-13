"use client";

/** Create, rename and delete the user's collections. Deleting keeps the documents. */

import { useState } from "react";
import { Button, Card, EmptyState, ErrorNotice, Skeleton } from "@/components/ui";
import {
  useCollections,
  useCreateCollection,
  useDeleteCollection,
  useRenameCollection,
} from "@/lib/queries/library";

export function ManageCollections() {
  const collections = useCollections();
  const create = useCreateCollection();
  const rename = useRenameCollection();
  const remove = useDeleteCollection();
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<{ id: number; name: string } | null>(null);

  const submitCreate = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    create.mutate({ name: trimmed }, { onSuccess: () => setName("") });
  };

  const submitRename = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editing) return;
    const trimmed = editing.name.trim();
    if (!trimmed) return;
    rename.mutate({ id: editing.id, name: trimmed }, { onSuccess: () => setEditing(null) });
  };

  const error = create.error ?? rename.error ?? remove.error;

  return (
    <Card title="Collections" className="min-w-0">
      <div data-testid="collections-manager" className="space-y-3 p-4">
        <form onSubmit={submitCreate} className="flex gap-2">
          <input
            aria-label="New collection name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={120}
            placeholder="New collection"
            className="min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1 text-sm"
          />
          <Button type="submit" variant="primary" disabled={create.isPending || !name.trim()}>
            Create
          </Button>
        </form>

        {error && <ErrorNotice error={error} />}

        {collections.isLoading ? (
          <Skeleton rows={3} />
        ) : collections.error ? (
          <ErrorNotice error={collections.error} onRetry={() => collections.refetch()} />
        ) : (collections.data ?? []).length === 0 ? (
          <EmptyState title="No collections yet" hint="Create one to group related documents." />
        ) : (
          <ul className="divide-y divide-line">
            {(collections.data ?? []).map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-2 py-2">
                {editing?.id === c.id ? (
                  <form onSubmit={submitRename} className="flex min-w-0 flex-1 gap-2">
                    <input
                      aria-label={`Rename ${c.name}`}
                      value={editing.name}
                      onChange={(e) => setEditing({ id: c.id, name: e.target.value })}
                      maxLength={120}
                      className="min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1 text-sm"
                    />
                    <Button type="submit" variant="primary" disabled={rename.isPending}>
                      Save
                    </Button>
                    <Button variant="ghost" onClick={() => setEditing(null)}>
                      Cancel
                    </Button>
                  </form>
                ) : (
                  <>
                    <span className="min-w-0 flex-1 truncate text-sm">
                      {c.name}
                      <span className="ml-2 text-xs text-muted">{c.document_count}</span>
                    </span>
                    <Button variant="ghost" onClick={() => setEditing({ id: c.id, name: c.name })}>
                      Rename
                    </Button>
                    <Button
                      variant="danger"
                      disabled={remove.isPending}
                      onClick={() => {
                        if (window.confirm(`Delete collection "${c.name}"? Its documents are kept.`)) {
                          remove.mutate(c.id);
                        }
                      }}
                    >
                      Delete
                    </Button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
