'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useAuthStore } from '../store/useAuthStore';
import { ApiError } from '../lib/api/types';
import { billingApi, creditApi, usersApi } from '../lib/api/endpoints';
import type { CreditData, Payment, User } from '../lib/api/types';

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

type Fetcher<T> = (signal: AbortSignal) => Promise<T>;

function useApi<T>(fetcher: Fetcher<T>, deps: ReadonlyArray<unknown> = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    (async () => {
      setLoading(true);
      setError(null);
      try {
        const result = await fetcher(controller.signal);
        if (mountedRef.current && !controller.signal.aborted) {
          setData(result);
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        if (mountedRef.current) {
          const message = err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Request failed';
          setError(message);
        }
      } finally {
        if (mountedRef.current && !controller.signal.aborted) {
          setLoading(false);
        }
      }
    })();

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const refetch = useCallback(() => setTick((t) => t + 1), []);
  return { data, loading, error, refetch };
}

// ── Session key ──────────────────────────────────────────────────────────────
//
// These three used to re-fetch when `useAuthStore.token` changed, which is no
// longer a thing that exists: the session is an httpOnly cookie and nothing
// about it is visible to JavaScript. The observable proxy for "the session
// changed" is now the identity the server reported, so that is what they key on
// — a sign-in, a sign-out, or a switch to a different account all move it, and a
// silent cookie refresh (same user) correctly does NOT trigger a refetch.
function useSessionKey(): string {
  const status = useAuthStore((s) => s.status);
  const userId = useAuthStore((s) => s.user?.id ?? '');
  return `${status}:${userId}`;
}

export function useUserProfile(): AsyncState<User> {
  return useApi<User>((signal) => usersApi.getMe(signal), [useSessionKey()]);
}

export function useCredit(): AsyncState<CreditData> {
  return useApi<CreditData>((signal) => creditApi.get(signal), [useSessionKey()]);
}

export function useBillingHistory(): AsyncState<Payment[]> {
  return useApi<Payment[]>((signal) => billingApi.history(signal), [useSessionKey()]);
}
