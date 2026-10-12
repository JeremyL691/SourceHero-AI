/** Queries and mutations for the library: collections, tags, document flags. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { API_URL, ApiError, api, clearToken, getToken } from "@/lib/api";
import type { components } from "@/lib/api-types";
import type { Document } from "@/lib/types";

type Schemas = components["schemas"];
export type Collection = Schemas["CollectionOut"];
export type Tag = Schemas["TagOut"];
export type DocumentFlags = Schemas["DocumentFlagsOut"];

/** The generated api wrapper has no body on DELETE, but the library routes take `{...}` there. */
async function deleteWithBody(path: string, body: unknown): Promise<void> {
  const token = getToken();
  const response = await fetch(`${API_URL}/v1${path}`, {
    method: "DELETE",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    credentials: "omit",
    cache: "no-store",
  });
  if (response.status === 401) {
    clearToken();
    throw new ApiError("Your session has expired. Please sign in again.", 401, "unauthenticated");
  }
  if (!response.ok) {
    let payload: { error?: { code?: string; message?: string } } | null = null;
    try {
      payload = await response.json();
    } catch {
      /* a non-JSON error body carries nothing we can show */
    }
    throw new ApiError(
      payload?.error?.message ?? `Request failed (${response.status})`,
      response.status,
      payload?.error?.code ?? "error",
    );
  }
}

// Reads ------------------------------------------------------------------

export function useCollections() {
  return useQuery({
    queryKey: ["collections"],
    queryFn: () => api.get<Collection[]>("/collections?limit=200"),
  });
}

export function useTags() {
  return useQuery({
    queryKey: ["tags"],
    queryFn: () => api.get<Tag[]>("/tags?limit=200"),
  });
}

/** Starred and archived state for one document: the detail returns the caller's own flags. */
export function useDocumentFlags(documentId: string) {
  return useQuery({
    queryKey: ["doc-flags", documentId],
    queryFn: async (): Promise<DocumentFlags> => {
      const doc = await api.get<Document>(`/documents/${documentId}`);
      return { document_id: documentId, starred: doc.starred ?? false, archived: doc.archived ?? false };
    },
  });
}

// Invalidation helpers ---------------------------------------------------

/** Anything that shows a document's tags or collections. */
function useInvalidateLibrary() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["library-docs"] });
    qc.invalidateQueries({ queryKey: ["doc"] });
    qc.invalidateQueries({ queryKey: ["collections"] });
    qc.invalidateQueries({ queryKey: ["tags"] });
  };
}

// Collection and tag management ------------------------------------------

export function useCreateCollection() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (input: { name: string; description?: string | null }) =>
      api.post<Collection>("/collections", input),
    onSuccess: invalidate,
  });
}

export function useRenameCollection() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      api.patch<Collection>(`/collections/${id}`, { name }),
    onSuccess: invalidate,
  });
}

export function useDeleteCollection() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/collections/${id}`),
    onSuccess: invalidate,
  });
}

export function useCreateTag() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (name: string) => api.post<Tag>("/tags", { name }),
    onSuccess: invalidate,
  });
}

export function useDeleteTag() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/tags/${id}`),
    onSuccess: invalidate,
  });
}

// Per-document actions ---------------------------------------------------

export function useAttachTag(documentId: string) {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (tagId: number) => api.post<void>(`/documents/${documentId}/tags`, { tag_id: tagId }),
    onSuccess: invalidate,
  });
}

export function useDetachTag(documentId: string) {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (tagId: number) =>
      deleteWithBody(`/documents/${documentId}/tags`, { tag_id: tagId }),
    onSuccess: invalidate,
  });
}

export function useAddToCollection(documentId: string) {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (collectionId: number) =>
      api.post<void>(`/collections/${collectionId}/items`, { document_id: documentId }),
    onSuccess: invalidate,
  });
}

export function useRemoveFromCollection(documentId: string) {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (collectionId: number) =>
      deleteWithBody(`/collections/${collectionId}/items`, { document_id: documentId }),
    onSuccess: invalidate,
  });
}

export function useSetDocumentFlags(documentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: { starred?: boolean; archived?: boolean }) =>
      api.patch<DocumentFlags>(`/documents/${documentId}/flags`, patch),
    onSuccess: (flags) => {
      qc.setQueryData(["doc-flags", documentId], flags);
      qc.invalidateQueries({ queryKey: ["library-docs"] });
    },
  });
}
