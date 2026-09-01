'use client';

// lib/fq/FqQueryProvider.tsx — the TanStack Query client for the Find Quant surface.
//
// Scoped on purpose. This provider exists for the session/message/run resources and nothing
// else: the app's other remote calls keep using `hooks/useApi.ts`, because rewriting working
// code to adopt a new library is churn that would put unrelated screens in this migration's
// blast radius.
//
// Created inside a `useState` initialiser rather than at module scope. A module-scope client
// is shared across every request in a Node server process, which on this deployment
// (`output: 'standalone'`, a long-lived server) would mean one user's cached session list
// could be served to another. The instance-per-mount form is the documented pattern for
// exactly that reason, and here the cache holds per-user conversation data, so it is a
// correctness requirement rather than a convention.

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

export function FqQueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // The terminal is a long-lived tab that users leave open across a trading
            // session. Refetching on every window focus would mean a burst of requests each
            // time someone alt-tabs back from their broker, so freshness is driven by
            // explicit invalidation at the points where stored rows actually change.
            refetchOnWindowFocus: false,
            // A session list is cheap to refetch and ordering matters, so it is not held
            // long; the per-query `staleTime` in `queries.ts` overrides where a resource is
            // genuinely immutable (a finished run's transcript).
            staleTime: 5_000,
            // Enough to ride out a proxy hiccup, few enough that a genuine failure surfaces
            // quickly. `queries.ts` additionally refuses to retry 401/404, which are answers
            // rather than failures.
            retry: 1,
          },
          mutations: {
            // A mutation is a user action. Silently retrying one risks a duplicate — a
            // second session, a second archive — so failures surface and the user decides.
            retry: 0,
          },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
