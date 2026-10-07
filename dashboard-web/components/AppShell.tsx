"use client";

/** The application frame: sidebar, global search, quick capture, theme toggle. */

import clsx from "clsx";
import {
  BookOpen,
  FileText,
  Inbox,
  LayoutDashboard,
  Moon,
  Plus,
  Scale,
  Search,
  BarChart2,
  Settings,
  Sun,
  Timer,
  ListChecks,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, clearToken } from "@/lib/api";
import { Button, Dialog } from "./ui";

const NAV = [
  { href: "/app", label: "Overview", icon: LayoutDashboard },
  { href: "/app/inbox", label: "Inbox", icon: Inbox },
  { href: "/app/sources", label: "Sources", icon: FileText },
  { href: "/app/library", label: "Library", icon: BookOpen },
  { href: "/app/ask", label: "Ask", icon: Search },
  { href: "/app/conflicts", label: "Conflicts", icon: Scale },
  { href: "/app/changes", label: "Changes", icon: Timer },
  { href: "/app/timeline", label: "Timeline", icon: Timer },
  { href: "/app/wiki", label: "Wiki", icon: BookOpen },
  { href: "/app/verify", label: "Verify", icon: ListChecks },
  { href: "/app/jobs", label: "Jobs", icon: ListChecks },
  { href: "/app/evals", label: "Evals", icon: BarChart2 },
  { href: "/app/settings", label: "Settings", icon: Settings },
];

type Theme = "light" | "dark" | "system";

function useTheme() {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => {
    const stored = window.localStorage.getItem("sh_theme") as Theme | null;
    if (stored) setTheme(stored);
  }, []);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") {
      root.removeAttribute("data-theme");
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      root.style.colorScheme = dark ? "dark" : "light";
    } else {
      root.setAttribute("data-theme", theme);
      root.style.colorScheme = theme;
    }
    window.localStorage.setItem("sh_theme", theme);
  }, [theme]);
  return { theme, setTheme };
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { theme, setTheme } = useTheme();
  const [captureOpen, setCaptureOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [captureText, setCaptureText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const capture = async () => {
    setBusy(true);
    setError(null);
    try {
      const trimmed = captureText.trim();
      const isUrl = /^https?:\/\/[^\s/$.?#].[^\s]*$/i.test(trimmed);
      if (isUrl) {
        await api.post("/captures", { url: trimmed });
      } else {
        await api.post("/captures", { text: trimmed });
      }
      setCaptureText("");
      setCaptureOpen(false);
      router.push("/app/sources");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-dvh">
      <div className="flex min-h-dvh flex-col lg:flex-row">
        <aside className="border-b border-line bg-surface lg:w-56 lg:border-r lg:border-b-0">
          <div className="flex items-center justify-between px-4 py-3">
            <Link href="/app" className="serif text-lg font-semibold tracking-tight">
              SourceHero
            </Link>
            <button
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
              className="text-muted hover:text-accent"
            >
              {theme === "dark" ? (
                <Sun className="h-4 w-4" aria-hidden />
              ) : (
                <Moon className="h-4 w-4" aria-hidden />
              )}
            </button>
          </div>
          <nav aria-label="Main" className="px-2 pb-3">
            <ul className="flex gap-1 overflow-x-auto lg:block lg:space-y-0.5 lg:overflow-visible">
              {NAV.map((item) => {
                const active = pathname === item.href;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={clsx(
                        "flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap",
                        active ? "bg-accent-soft font-medium text-accent" : "text-muted hover:bg-paper hover:text-ink",
                      )}
                    >
                      <item.icon className="h-4 w-4 shrink-0" aria-hidden />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <div className="hidden px-3 pb-4 lg:block">
            <Button
              variant="ghost"
              className="w-full justify-start"
              onClick={() => setCaptureOpen(true)}
            >
              <Plus className="h-4 w-4" aria-hidden />
              Quick capture
            </Button>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center gap-2 border-b border-line px-4 py-2">
            <Button variant="primary" onClick={() => setCaptureOpen(true)} className="lg:hidden">
              <Plus className="h-4 w-4" aria-hidden />
              Capture
            </Button>
            <form
              className="flex-1"
              onSubmit={(e) => {
                e.preventDefault();
                router.push(`/app/library?q=${encodeURIComponent(searchText)}`);
              }}
            >
              <input
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                placeholder="Search your sources…"
                aria-label="Search sources"
                className="w-full rounded-md border border-line bg-surface px-3 py-1.5 text-sm placeholder:text-muted/70"
              />
            </form>
            <Button
              variant="ghost"
              onClick={() => {
                queryClient.clear();
                clearToken();
                router.push("/login");
              }}
            >
              Sign out
            </Button>
          </header>
          <main id="main" className="min-w-0 flex-1 p-4 lg:p-6">
            {children}
          </main>
        </div>
      </div>

      <Dialog open={captureOpen} title="Quick capture" onClose={() => setCaptureOpen(false)}>
        <label className="block text-sm font-medium" htmlFor="capture-text">
          URL or text
        </label>
        <textarea
          id="capture-text"
          value={captureText}
          onChange={(e) => setCaptureText(e.target.value)}
          rows={4}
          className="mt-1 w-full rounded-md border border-line bg-paper p-2 text-sm"
          placeholder="https://example.com/article or enter a note…"
        />
        {error && <p className="mt-2 text-sm text-contradicted">{error}</p>}
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setCaptureOpen(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={capture} disabled={busy || !captureText.trim()}>
            {busy ? "Saving…" : "Capture"}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
