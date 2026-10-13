"use client";

/** Create and delete tags. Tags are attached to documents on the document page. */

import { useState } from "react";
import { Button, Card, EmptyState, ErrorNotice, Skeleton } from "@/components/ui";
import { useCreateTag, useDeleteTag, useTags } from "@/lib/queries/library";

export function ManageTags() {
  const tags = useTags();
  const create = useCreateTag();
  const remove = useDeleteTag();
  const [name, setName] = useState("");

  const submitCreate = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    create.mutate(trimmed, { onSuccess: () => setName("") });
  };

  const error = create.error ?? remove.error;

  return (
    <Card title="Tags">
      <div data-testid="k6-tags-manager" className="space-y-3 p-4">
        <form onSubmit={submitCreate} className="flex gap-2">
          <input
            aria-label="New tag name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={64}
            placeholder="New tag"
            className="min-w-0 flex-1 rounded border border-line bg-surface px-2 py-1 text-sm"
          />
          <Button type="submit" variant="primary" disabled={create.isPending || !name.trim()}>
            Create
          </Button>
        </form>

        {error && <ErrorNotice error={error} />}

        {tags.isLoading ? (
          <Skeleton rows={2} />
        ) : tags.error ? (
          <ErrorNotice error={tags.error} onRetry={() => tags.refetch()} />
        ) : (tags.data ?? []).length === 0 ? (
          <EmptyState title="No tags yet" hint="Create a tag, then attach it to documents." />
        ) : (
          <ul className="flex flex-wrap gap-2">
            {(tags.data ?? []).map((t) => (
              <li
                key={t.id}
                className="flex items-center gap-1 rounded-full border border-line px-2.5 py-0.5 text-xs"
              >
                <span>{t.name}</span>
                <button
                  aria-label={`Delete tag ${t.name}`}
                  disabled={remove.isPending}
                  onClick={() => {
                    if (window.confirm(`Delete tag "${t.name}" from every document?`)) {
                      remove.mutate(t.id);
                    }
                  }}
                  className="text-muted hover:text-contradicted"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
