/** Typed fetch wrapper.
 *
 * Two things it guarantees, because everything else depends on them:
 *  - every request carries `Authorization`;
 *  - a 401 clears the stored token and sends the user to /login rather than leaving
 *    the UI half-authenticated, failing silently on the next click.
 */

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8787").replace(
  /\/$/,
  "",
);

const TOKEN_KEY = "sh_token";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 7;

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

/** Store the token in localStorage *and* as a cookie.
 *
 * The cookie is what the route guard reads on the server; localStorage is what the
 * client reads. SameSite=Lax so a navigation from another site cannot ride it. */
export function setToken(token: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, token);
  document.cookie = `${TOKEN_KEY}=${token}; path=/; max-age=${COOKIE_MAX_AGE}; samesite=lax`;
}

export function clearToken() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
  document.cookie = `${TOKEN_KEY}=; path=/; max-age=0; samesite=lax`;
}

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;

  constructor(message: string, status: number, code = "error", details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function toError(status: number, body: unknown): ApiError {
  const payload = body as { error?: { message?: string; code?: string; details?: unknown }; detail?: unknown };
  const message =
    payload?.error?.message ||
    (typeof payload?.detail === "string" ? payload.detail : null) ||
    `Request failed (${status})`;
  const code = payload?.error?.code || "error";
  return new ApiError(message, status, code, payload?.error?.details);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData;
  const headers: Record<string, string> = {
    ...authHeaders(),
    ...((init.headers as Record<string, string>) || {}),
  };
  if (!isFormData && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(`${API_URL}/v1${path}`, {
    ...init,
    headers,
    // Cookies would let a cross-site POST ride the session; the bearer token is the
    // only credential we send.
    credentials: "omit",
    cache: "no-store",
  });

  if (response.status === 401) {
    if (!path.startsWith("/auth/login")) {
      clearToken();
      if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
        window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
      }
      throw new ApiError("Your session has expired. Please sign in again.", 401, "unauthenticated");
    }
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      /* a non-JSON error body carries nothing we can show */
    }
    throw toError(response.status, body);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  put: <T,>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  patch: <T,>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
  form: <T,>(path: string, body: FormData) => request<T>(path, { method: "POST", body }),
  blob: async (path: string): Promise<Blob> => {
    const headers: Record<string, string> = {
      ...authHeaders(),
    };
    const response = await fetch(`${API_URL}/v1${path}`, {
      headers,
      credentials: "omit",
      cache: "no-store",
    });
    if (!response.ok) {
      let body: unknown;
      try {
        body = await response.json();
      } catch {
        body = null;
      }
      throw toError(response.status, body);
    }
    return await response.blob();
  },
};

export function parseSingleSSEFrame(frame: string): { event: string; data: unknown } | null {
  const lines = frame.split("\n");
  let eventType = "message";
  const dataLines: string[] = [];

  for (const line of lines) {
    if (!line || line.startsWith(":")) continue;
    const colonIndex = line.indexOf(":");
    let field = line;
    let value = "";
    if (colonIndex !== -1) {
      field = line.slice(0, colonIndex);
      value = line.slice(colonIndex + 1);
      if (value.startsWith(" ")) {
        value = value.slice(1);
      }
    }
    if (field === "event") {
      eventType = value.trim();
    } else if (field === "data") {
      dataLines.push(value);
    }
  }

  if (dataLines.length === 0 && eventType === "message") {
    return null;
  }

  const rawData = dataLines.join("\n");
  let parsedData: unknown = rawData;
  try {
    parsedData = JSON.parse(rawData);
  } catch {
    /* raw string fallback */
  }

  return { event: eventType, data: parsedData };
}

export function parseSSEFrames(buffer: string): {
  events: Array<{ event: string; data: unknown }>;
  remaining: string;
} {
  const normalized = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const parts = normalized.split("\n\n");
  const remaining = parts.pop() ?? "";
  const events: Array<{ event: string; data: unknown }> = [];

  for (const part of parts) {
    const parsed = parseSingleSSEFrame(part);
    if (parsed) events.push(parsed);
  }
  return { events, remaining };
}

/** Read an SSE stream from a POST endpoint.
 *
 * `EventSource` cannot be used: it only issues GET and cannot send an
 * `Authorization` header, and every call here needs one. So this reads the
 * `ReadableStream` and parses `event:`/`data:` frames itself.
 */
export async function streamSSE(
  path: string,
  body: unknown,
  onEvent: (event: string, data: unknown) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_URL}/v1${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    credentials: "omit",
    cache: "no-store",
    body: JSON.stringify(body),
    signal,
  });

  if (response.status === 401) {
    if (!path.startsWith("/auth/login")) {
      clearToken();
      if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
        window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
      }
    }
    throw new ApiError("Your session has expired. Please sign in again.", 401, "unauthenticated");
  }
  if (!response.ok || !response.body) {
    let errBody: unknown = null;
    try {
      errBody = await response.json();
    } catch {}
    throw toError(response.status, errBody);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        if (buffer.trim()) {
          const finalEvent = parseSingleSSEFrame(buffer);
          if (finalEvent) {
            onEvent(finalEvent.event, finalEvent.data);
          }
        }
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const { events, remaining } = parseSSEFrames(buffer);
      buffer = remaining;
      for (const ev of events) {
        onEvent(ev.event, ev.data);
      }
    }
  } finally {
    reader.releaseLock();
  }
}
