import { API_BASE_URL, API_V1_PREFIX } from '../env';
import { useAuthStore } from '../../store/useAuthStore';
import { ApiError, type ApiResponse } from './types';

const ACCESS_TOKEN_KEY = 'token';
const REFRESH_TOKEN_KEY = 'strat_refresh_token';

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  auth?: boolean;
  headers?: Record<string, string>;
};

let refreshPromise: Promise<string> | null = null;

function readStoredRefreshToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

function persistTokens(accessToken: string, refreshToken: string): void {
  if (typeof window === 'undefined') return;
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  useAuthStore.setState({ token: accessToken, refreshToken });
}

async function refreshAccessToken(): Promise<string> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    const refreshToken = readStoredRefreshToken() ?? useAuthStore.getState().refreshToken;
    if (!refreshToken) {
      throw new ApiError('No refresh token available', 401);
    }

    const res = await fetch(`${API_BASE_URL}${API_V1_PREFIX}/auth/refresh-token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh: refreshToken }),
    });

    const json = (await res.json().catch(() => null)) as ApiResponse<{ accessToken: string; refreshToken: string }> | null;
    if (!res.ok || !json?.success || !json.data?.accessToken || !json.data?.refreshToken) {
      useAuthStore.getState().logout();
      throw new ApiError(json?.message ?? 'Token refresh failed', res.status);
    }

    persistTokens(json.data.accessToken, json.data.refreshToken);
    return json.data.accessToken;
  })().finally(() => {
    refreshPromise = null;
  });

  return refreshPromise;
}

async function parseEnvelope<T>(res: Response): Promise<T> {
  const json = (await res.json().catch(() => null)) as ApiResponse<T> | null;
  if (!json) {
    throw new ApiError('Invalid response from server', res.status);
  }
  if (!json.success) {
    throw new ApiError(json.message || `Request failed with status ${res.status}`, res.status);
  }
  return json.data;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, auth = true, headers = {} } = options;

  const url = `${API_BASE_URL}${API_V1_PREFIX}${path}`;
  const finalHeaders: Record<string, string> = { ...headers };
  if (body !== undefined && !finalHeaders['Content-Type']) {
    finalHeaders['Content-Type'] = 'application/json';
  }
  if (auth) {
    const token = useAuthStore.getState().token;
    if (token) finalHeaders['Authorization'] = `Bearer ${token}`;
  }

  const init: RequestInit = {
    method,
    headers: finalHeaders,
    signal,
  };
  if (body !== undefined) init.body = JSON.stringify(body);

  const res = await fetch(url, init);

  if (res.status === 401 && auth) {
    const newToken = await refreshAccessToken();
    finalHeaders['Authorization'] = `Bearer ${newToken}`;
    const retryInit: RequestInit = { method, headers: finalHeaders, signal };
    if (body !== undefined) retryInit.body = JSON.stringify(body);
    const retryRes = await fetch(url, retryInit);
    return parseEnvelope<T>(retryRes);
  }

  return parseEnvelope<T>(res);
}

export { REFRESH_TOKEN_KEY, ACCESS_TOKEN_KEY };
