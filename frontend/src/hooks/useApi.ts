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

export function useUserProfile(): AsyncState<User> {
  const token = useAuthStore((s) => s.token);
  return useApi<User>((signal) => usersApi.getMe(signal), [token]);
}

export function useCredit(): AsyncState<CreditData> {
  const token = useAuthStore((s) => s.token);
  return useApi<CreditData>((signal) => creditApi.get(signal), [token]);
}

export function useBillingHistory(): AsyncState<Payment[]> {
  const token = useAuthStore((s) => s.token);
  return useApi<Payment[]>((signal) => billingApi.history(signal), [token]);
}
