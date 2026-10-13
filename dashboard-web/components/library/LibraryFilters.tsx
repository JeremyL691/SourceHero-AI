"use client";

/** Filter controls for the library. The page owns the state (it lives in the URL). */

import { useState } from "react";
import { Button } from "@/components/ui";
import type { Collection, Tag } from "@/lib/queries/library";

/** Source kinds as the backend stores them (app/models/source.py SOURCE_TYPES). */
const SOURCE_TYPES = ["webpage", "rss", "pdf", "file", "note", "clip", "conversation", "demo"];

export interface LibraryFilterValues {
  source_type: string;
  tag_id: string;
  collection_id: string;
  starred: string;
  archived: string;
  q: string;
}

const selectClass = "rounded border border-line bg-surface px-2 py-1 text-sm";

export function LibraryFilters({
  values,
  tags,
  collections,
  onChange,
}: {
  values: LibraryFilterValues;
  tags: Tag[];
  collections: Collection[];
  onChange: (patch: Partial<LibraryFilterValues>) => void;
}) {
  const [keyword, setKeyword] = useState(values.q);

  return (
    <div data-testid="library-filters" className="flex flex-wrap items-end gap-3 text-xs text-muted">
      <label className="flex flex-col gap-1">
        Source type
        <select
          data-testid="filter-source-type"
          value={values.source_type}
          onChange={(e) => onChange({ source_type: e.target.value })}
          className={selectClass}
        >
          <option value="">Any</option>
          {SOURCE_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1">
        Tag
        <select
          data-testid="filter-tag"
          value={values.tag_id}
          onChange={(e) => onChange({ tag_id: e.target.value })}
          className={selectClass}
        >
          <option value="">Any</option>
          {tags.map((t) => (
            <option key={t.id} value={String(t.id)}>
              {t.name}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1">
        Collection
        <select
          data-testid="filter-collection"
          value={values.collection_id}
          onChange={(e) => onChange({ collection_id: e.target.value })}
          className={selectClass}
        >
          <option value="">Any</option>
          {collections.map((c) => (
            <option key={c.id} value={String(c.id)}>
              {c.name}
            </option>
          ))}
        </select>
      </label>

      <label className="flex flex-col gap-1">
        Starred
        <select
          data-testid="filter-starred"
          value={values.starred}
          onChange={(e) => onChange({ starred: e.target.value })}
          className={selectClass}
        >
          <option value="">Any</option>
          <option value="true">Starred only</option>
          <option value="false">Not starred</option>
        </select>
      </label>

      <label className="flex flex-col gap-1">
        Archived
        <select
          data-testid="filter-archived"
          value={values.archived}
          onChange={(e) => onChange({ archived: e.target.value })}
          className={selectClass}
        >
          <option value="">Any</option>
          <option value="true">Archived only</option>
          <option value="false">Hide archived</option>
        </select>
      </label>

      <form
        className="flex flex-col gap-1"
        onSubmit={(e) => {
          e.preventDefault();
          onChange({ q: keyword.trim() });
        }}
      >
        Keyword
        <span className="flex gap-2">
          <input
            data-testid="filter-keyword"
            type="search"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            maxLength={200}
            placeholder="Title or URL"
            className={`${selectClass} w-48 text-ink`}
          />
          <Button type="submit">Search</Button>
        </span>
      </form>
    </div>
  );
}
