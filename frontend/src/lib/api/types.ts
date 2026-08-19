export type UserRole = 'user' | 'admin';
export type UserStatus = 'active' | 'blocked' | 'deleted';
export type PaymentStatus = 'pending' | 'paid' | 'failed' | 'refunded';
export type PaymentType = 'subscription' | 'topup';

export interface User {
  id: string;
  email: string;
  username: string;
  name: string;
  role: UserRole;
  status: UserStatus;
  createdAt: string;
  updatedAt: string;
}

export interface AccessFlags {
  canAccessDeepseekGLM: boolean;
  canAccessMultiModel: boolean;
  canAccessGhostline: boolean;
  canAccessFootprint: boolean;
  canAccessTopup: boolean;
  canSeeInstantNewsSantiments: boolean;
  canGetAdvanceChartAccess: boolean;
  /**
   * RESEARCH SKU entitlement — grants the regulated recommendation surface
   * (FIND, DEBATE, QA, conviction score, journal). See `lib/sku.ts`.
   *
   * Optional because the remote credit API does not emit it yet. Absent or
   * anything other than boolean `true` resolves to the TERMINAL SKU, so the
   * default is fail-closed. The authoritative check is server-side in
   * `agents/deep-quant-loop/entitlements.py`; this flag drives UI state only.
   */
  canAccessResearch?: boolean;
}

export interface CreditLog {
  id: string;
  userId: string;
  amount: number;
  previousBalance: number;
  newBalance: number;
  type: string;
  description: string;
  createdAt: string;
}

export interface CreditData {
  hasActiveSubscription: boolean;
  credits: number;
  planName: string;
  expiresAt: string | null;
  accessFlags: AccessFlags;
  creditMultiplier: number | null;
  creditLogs: CreditLog[];
}

export interface PaymentStatusHistory {
  id: string;
  paymentId: string;
  status: PaymentStatus;
  createdAt: string;
}

export interface Payment {
  id: string;
  userId: string;
  invoiceId: string;
  gatewayPaymentId: string;
  gatewayOrderId: string;
  webhookEventId: string | null;
  processing: boolean;
  amount: number;
  type: PaymentType;
  planId: string | null;
  topupCredits: number | null;
  createdAt: string;
  updatedAt: string;
  statusHistory?: PaymentStatusHistory[];
}

export interface PaymentWithUser extends Payment {
  user?: { id: string; name: string; email: string };
  statusHistory: PaymentStatusHistory[];
}

export interface Plan {
  id: string;
  name: string;
  priceINR: number;
  creditsGiven: number;
  description: string;
  canAccessDeepseekGLM: boolean;
  canAccessMultiModel: boolean;
  canAccessGhostline: boolean;
  canAccessFootprint: boolean;
  canAccessTopup: boolean;
  canSeeInstantNewsSantiments: boolean;
  canGetAdvanceChartAccess: boolean;
  creditMultiplier: number | null;
  deletedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ApiResponse<T> {
  success: boolean;
  message: string;
  data: T;
}

export interface ApiErrorEnvelope {
  success: false;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export function latestPaymentStatus(payment: Payment): PaymentStatus | null {
  const history = payment.statusHistory;
  if (!history || history.length === 0) return null;
  // The backend does not guarantee a stable order for statusHistory entries,
  // so pick the entry with the latest createdAt timestamp rather than the
  // last array element.
  let latest = history[0];
  for (let i = 1; i < history.length; i++) {
    const entry = history[i];
    if (!latest.createdAt || (entry.createdAt && entry.createdAt > latest.createdAt)) {
      latest = entry;
    }
  }
  return latest.status;
}
