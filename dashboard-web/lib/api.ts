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

  constructor(message: string, status: number, code = "error") {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function toError(status: number, body: unknown): ApiError {
  const payload = body as { error?: { message?: string; code?: string }; detail?: unknown };
  const message =
    payload?.error?.message ||
    (typeof payload?.detail === "string" ? payload.detail : null) ||
    `Request failed (${status})`;
  const code = payload?.error?.code || "error";
  return new ApiError(message, status, code);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init.headers || {}),
    },
    // Cookies would let a cross-site POST ride the session; the bearer token is the
    // only credential we send.
    credentials: "omit",
    cache: "no-store",
  });

  if (response.status === 401) {
    clearToken();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    }
    throw new ApiError("Your session has expired. Please sign in again.", 401, "unauthenticated");
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
};

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
    clearToken();
    throw new ApiError("Your session has expired. Please sign in again.", 401, "unauthenticated");
  }
  if (!response.ok || !response.body) {
    throw new ApiError(`Stream failed (${response.status})`, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line; a partial frame stays in the buffer.
    let split = buffer.indexOf("\n\n");
    while (split !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const event = /event:\s*(.+)/.exec(frame)?.[1]?.trim();
      const data = /data:\s*([\s\S]*)/.exec(frame)?.[1]?.trim();
      if (event && data) {
        try {
          onEvent(event, JSON.parse(data));
        } catch {
          /* a frame that is not JSON is not worth failing the stream over */
        }
      }
      split = buffer.indexOf("\n\n");
    }
  }
}
