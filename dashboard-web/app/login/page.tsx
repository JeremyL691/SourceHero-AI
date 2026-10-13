"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { api, ApiError, setToken } from "@/lib/api";
import type { User } from "@/lib/types";
import { Button } from "@/components/ui";

export function sanitizeNextPath(raw: string | null): string {
  if (!raw) return "/app";
  if (raw.startsWith("/") && !raw.startsWith("//")) {
    return raw;
  }
  return "/app";
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = sanitizeNextPath(params.get("next"));

  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const path = mode === "login" ? "/auth/login" : "/auth/register";
      const result = await api.post<{ access_token: string; user: User }>(path, {
        email,
        password,
      });
      setToken(result.access_token);
      router.push(next);
      router.refresh();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : "Could not reach the API. Is it running on the configured port?",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main id="main" className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-6 py-16">
      <Link href="/" className="serif text-lg font-semibold">
        SourceHero
      </Link>
      <h1 className="serif mt-6 text-2xl font-semibold">
        {mode === "login" ? "Sign in" : "Create an account"}
      </h1>
      <p className="mt-1 text-sm text-muted">
        {mode === "login"
          ? "Your workspace runs on your own machine."
          : "The first account owns this instance."}
      </p>

      <form onSubmit={submit} className="mt-6 space-y-3">
        <div>
          <label htmlFor="email" className="block text-sm font-medium">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label htmlFor="password" className="block text-sm font-medium">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            minLength={mode === "register" ? 10 : undefined}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
          />
          {mode === "register" && (
            <p className="mt-1 text-xs text-muted">At least 10 characters.</p>
          )}
        </div>
        {error && (
          <p role="alert" className="text-sm text-contradicted">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" disabled={busy} className="w-full justify-center">
          {busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}
        </Button>
      </form>

      <button
        onClick={() => setMode(mode === "login" ? "register" : "login")}
        className="mt-4 text-sm text-muted underline underline-offset-2 hover:text-accent"
      >
        {mode === "login" ? "Need an account?" : "Already have an account?"}
      </button>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<main id="main" className="p-8 text-sm text-muted">Loading…</main>}>
      <LoginForm />
    </Suspense>
  );
}
