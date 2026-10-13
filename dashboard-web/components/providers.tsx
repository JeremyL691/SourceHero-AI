"use client";

/** Query client and the toast surface, mounted once in the root layout. */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

const ToastContext = createContext<(message: string) => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

export function Providers({ children }: { children: React.ReactNode }) {
  // Created in state, not at module scope: a shared client across requests leaks one
  // user's cached documents into another's session on the server.
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 },
        },
      }),
  );
  const [toasts, setToasts] = useState<string[]>([]);

  const push = useCallback((message: string) => {
    setToasts((current) => [...current, message]);
    setTimeout(() => setToasts((current) => current.slice(1)), 4000);
  }, []);

  const value = useMemo(() => push, [push]);

  return (
    <QueryClientProvider client={client}>
      <ToastContext.Provider value={value}>
        {children}
        <div className="pointer-events-none fixed right-4 bottom-4 z-50 space-y-2">
          {toasts.map((toast, i) => (
            <div
              key={i}
              role="status"
              className="pointer-events-auto rounded-md border border-line bg-surface px-3 py-2 text-sm shadow-sm"
            >
              {toast}
            </div>
          ))}
        </div>
      </ToastContext.Provider>
    </QueryClientProvider>
  );
}
