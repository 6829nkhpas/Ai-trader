/**
 * api-client.ts
 * ─────────────────────────────────────────────────────────────────────────────
 * Axios instance pre-configured for the AI-Trade auth service.
 *
 * Security invariants enforced here:
 *  ✅ withCredentials: true  — sends HttpOnly session cookie on every request
 *  ✅ JWT is NEVER read from / written to localStorage or sessionStorage
 *  ✅ 401 interceptor drives a single refresh-token rotation attempt
 *  ✅ If refresh fails, all pending requests are rejected and user is
 *     redirected to /auth/login
 */

import axios, {
  AxiosError,
  AxiosInstance,
  AxiosRequestConfig,
  InternalAxiosRequestConfig,
} from 'axios';

// ─────────────────────────────────────────────────────────────────────────────
// Base configuration
// ─────────────────────────────────────────────────────────────────────────────

const AUTH_BASE_URL =
  process.env.NEXT_PUBLIC_AUTH_API_URL ?? 'http://localhost:3001';

export const apiClient: AxiosInstance = axios.create({
  baseURL: AUTH_BASE_URL,
  withCredentials: true,      // ← critical: sends HttpOnly refresh_token cookie
  timeout: 10_000,
  headers: {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  },
});

// ─────────────────────────────────────────────────────────────────────────────
// Refresh token rotation state
// ─────────────────────────────────────────────────────────────────────────────

let isRefreshing = false;
let refreshSubscribers: Array<(success: boolean) => void> = [];

function subscribeTokenRefresh(cb: (success: boolean) => void) {
  refreshSubscribers.push(cb);
}

function notifySubscribers(success: boolean) {
  refreshSubscribers.forEach((cb) => cb(success));
  refreshSubscribers = [];
}

// ─────────────────────────────────────────────────────────────────────────────
// 401 Response interceptor — Automatic Refresh Token Rotation
// ─────────────────────────────────────────────────────────────────────────────

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & {
      _retry?: boolean;
    };

    const is401 = error.response?.status === 401;
    const isRefreshEndpoint =
      originalRequest.url?.includes('/api/auth/refresh') ||
      originalRequest.url?.includes('/api/auth/login') ||
      originalRequest.url?.includes('/api/auth/logout');

    // Pass through non-401 errors and errors from the refresh endpoint itself
    if (!is401 || isRefreshEndpoint || originalRequest._retry) {
      return Promise.reject(error);
    }

    // Mark request so we don't loop
    originalRequest._retry = true;

    if (isRefreshing) {
      // Queue this request until the ongoing refresh resolves
      return new Promise((resolve, reject) => {
        subscribeTokenRefresh((success) => {
          if (success) {
            resolve(apiClient(originalRequest));
          } else {
            reject(error);
          }
        });
      });
    }

    isRefreshing = true;

    try {
      // Attempt silent token refresh — backend reads HttpOnly refresh_token cookie
      await axios.post(
        `${AUTH_BASE_URL}/api/auth/refresh`,
        {},
        { withCredentials: true }
      );

      notifySubscribers(true);
      isRefreshing = false;

      // Retry the original failed request
      return apiClient(originalRequest);
    } catch (refreshError) {
      notifySubscribers(false);
      isRefreshing = false;

      // Session is dead — redirect to login
      if (typeof window !== 'undefined') {
        window.location.href = '/auth/login?reason=session_expired';
      }

      return Promise.reject(refreshError);
    }
  }
);

// ─────────────────────────────────────────────────────────────────────────────
// Typed API helpers
// ─────────────────────────────────────────────────────────────────────────────

export interface LoginPayload {
  email: string;
  password: string;
}

export interface SignupPayload {
  email: string;
  password: string;
}

export interface MfaVerifyPayload {
  code: string;
}

export interface GoogleOAuthPayload {
  /** ID token returned by Google's OAuth 2.0 PKCE flow */
  idToken: string;
}

export const authApi = {
  login: (payload: LoginPayload) =>
    apiClient.post('/api/auth/login', payload),

  signup: (payload: SignupPayload) =>
    apiClient.post('/api/auth/register', payload),

  logout: () => apiClient.post('/api/auth/logout'),

  refresh: () => apiClient.post('/api/auth/refresh'),

  session: () => apiClient.get('/api/auth/session'),

  mfaVerify: (payload: MfaVerifyPayload) =>
    apiClient.post('/api/auth/mfa/verify', payload),

  mfaSetup: () => apiClient.post('/api/auth/mfa/setup'),

  googleOAuth: (payload: GoogleOAuthPayload) =>
    apiClient.post('/api/auth/oauth/google', payload),
} as const;

export type { AxiosRequestConfig };
