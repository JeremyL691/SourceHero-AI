"use client";

/** Star, archive, and attach or detach tags and collections for one document. */

import { Button, ErrorNotice } from "@/components/ui";
import {
  useAddToCollection,
  useAttachTag,
  useCollections,
  useDetachTag,
  useDocumentFlags,
  useRemoveFromCollection,
  useSetDocumentFlags,
  useTags,
} from "@/lib/queries/library";

interface NamedRef {
  id: number;
  name: string;
}

const chipClass =
  "inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-xs text-ink";
const selectClass = "rounded border border-line bg-surface px-1.5 py-0.5 text-xs";

export function DocumentOrganizer({
  documentId,
  tags,
  collections,
}: {
  documentId: string;
  tags: NamedRef[];
  collections: NamedRef[];
}) {
  const allTags = useTags();
  const allCollections = useCollections();
  const flags = useDocumentFlags(documentId);
  const setFlags = useSetDocumentFlags(documentId);
  const attach = useAttachTag(documentId);
  const detach = useDetachTag(documentId);
  const addTo = useAddToCollection(documentId);
  const removeFrom = useRemoveFromCollection(documentId);

  const attachedTagIds = new Set(tags.map((t) => t.id));
  const attachableTags = (allTags.data ?? []).filter((t) => !attachedTagIds.has(t.id));
  const attachedCollectionIds = new Set(collections.map((c) => c.id));
  const addableCollections = (allCollections.data ?? []).filter((c) => !attachedCollectionIds.has(c.id));

  const failure =
    setFlags.error ?? attach.error ?? detach.error ?? addTo.error ?? removeFrom.error ?? flags.error;

  return (
    <section aria-label="Organise" className="space-y-3 rounded-lg border border-line bg-surface p-4">
      <div className="flex flex-wrap gap-2">
        <Button
          data-testid="doc-star"
          aria-pressed={!!flags.data?.starred}
          disabled={flags.isLoading || !!flags.error || setFlags.isPending}
          onClick={() => setFlags.mutate({ starred: !flags.data?.starred })}
        >
          {flags.data?.starred ? "Unstar" : "Star"}
        </Button>
        <Button
          data-testid="doc-archive"
          aria-pressed={!!flags.data?.archived}
          disabled={flags.isLoading || !!flags.error || setFlags.isPending}
          onClick={() => setFlags.mutate({ archived: !flags.data?.archived })}
        >
          {flags.data?.archived ? "Unarchive" : "Archive"}
        </Button>
      </div>

      <div data-testid="doc-tags" className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">Tags</span>
        {tags.map((t) => (
          <span key={t.id} className={chipClass}>
            {t.name}
            <button
              aria-label={`Remove tag ${t.name}`}
              disabled={detach.isPending}
              onClick={() => detach.mutate(t.id)}
              className="text-muted hover:text-contradicted"
            >
              ×
            </button>
          </span>
        ))}
        <select
          aria-label="Add a tag"
          data-testid="doc-tag-add"
          value=""
          disabled={attachableTags.length === 0 || attach.isPending}
          onChange={(e) => {
            const id = Number(e.target.value);
            if (id) attach.mutate(id);
          }}
          className={selectClass}
        >
          <option value="">{attachableTags.length === 0 ? "No tags to add" : "Add tag…"}</option>
          {attachableTags.map((t) => (
            <option key={t.id} value={String(t.id)}>
              {t.name}
            </option>
          ))}
        </select>
      </div>

      <div data-testid="doc-collections" className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">Collections</span>
        {collections.map((c) => (
          <span key={c.id} className={chipClass}>
            {c.name}
            <button
              aria-label={`Remove from collection ${c.name}`}
              disabled={removeFrom.isPending}
              onClick={() => removeFrom.mutate(c.id)}
              className="text-muted hover:text-contradicted"
            >
              ×
            </button>
          </span>
        ))}
        <select
          aria-label="Add to a collection"
          data-testid="doc-collection-add"
          value=""
          disabled={addableCollections.length === 0 || addTo.isPending}
          onChange={(e) => {
            const id = Number(e.target.value);
            if (id) addTo.mutate(id);
          }}
          className={selectClass}
        >
          <option value="">
            {addableCollections.length === 0 ? "No collections to add" : "Add to collection…"}
          </option>
          {addableCollections.map((c) => (
            <option key={c.id} value={String(c.id)}>
              {c.name}
            </option>
          ))}
        </select>
      </div>

      {failure && <ErrorNotice error={failure} />}
    </section>
  );
}
