import { apiRequest } from './client';
import type { CreditData, Payment, PaymentWithUser, User } from './types';

export const usersApi = {
  getMe: (signal?: AbortSignal) => apiRequest<User>('/users/me', { signal }),
  updateMe: (payload: { name: string }, signal?: AbortSignal) =>
    apiRequest<User>('/users/me', { method: 'PATCH', body: payload, signal }),
};

export const creditApi = {
  get: (signal?: AbortSignal) => apiRequest<CreditData>('/credit/', { signal }),
};

export const billingApi = {
  history: (signal?: AbortSignal) => apiRequest<Payment[]>('/billing/history', { signal }),
  get: (id: string, signal?: AbortSignal) =>
    apiRequest<PaymentWithUser>(`/billing/${id}`, { signal }),
};
