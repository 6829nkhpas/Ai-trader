// lib/fq/api.ts — typed client for the Find Quant session surface.
//
// Same-origin only. These go to `/api/deepquant/sessions*`, which the Next route tier
// proxies after resolving the caller's identity from the httpOnly session cookie and
// minting an internal assertion. So there is deliberately NO `user_id` anywhere in this
// file: the caller cannot choose who they are, which is the point of the migration.
//
// Why a plain fetch module rather than the existing `lib/api/client.ts`
// -------------------------------------------------------------------
// That client targets the REMOTE auth/credit API (`api-web.stratai.live`) and carries
// `credentials: 'include'` plus a refresh-on-401 retry for a cross-origin cookie. These
// calls are same-origin and the cookie rides along by default; borrowing that client would
// bring a refresh loop that does not apply and an envelope shape (`{success, data}`) these
// routes do not use.
//
// This module is intentionally thin. Caching, invalidation and optimistic updates belong to
// the query layer (Phase 7), not here — a second ad-hoc cache is exactly what the migration
// plan forbids.

const BASE = '/api/deepquant';

/** What the tab bar and the history list need, and nothing more. */
export interface SessionSummary {
  session_id: string;
  title: string | null;
  symbol: string;
  timeframe: string;
  profile: string;
  status: 'active' | 'archived' | 'deleted';
  created_at: number;
  updated_at: number;
  archived_at: number | null;
  active_run_id: string | null;
  message_count: number;
  last_run: {
    run_id: string;
    kind: 'find' | 'verify';
    status: string;
    started_at: number;
    ended_at: number | null;
  } | null;
}

export interface SessionListPage {
  items: SessionSummary[];
  next_cursor: string | null;
}

export interface StoredMessage {
  message_id: string;
  session_id: string;
  run_id: string | null;
  seq: number;
  role: 'user' | 'assistant' | 'system';
  kind: 'analysis_request' | 'analysis_answer' | 'qa_question' | 'qa_answer' | 'notice';
  content: string;
  /**
   * `truncated` is the one that matters: a half-received answer must be rendered as such,
   * never as a completed one. `streaming` on a freshly-loaded session means the run is
   * genuinely still producing.
   */
  status: 'streaming' | 'complete' | 'truncated' | 'error' | 'cancelled';
  error_detail: string | null;
  activity: string[] | null;
  client_msg_id: string | null;
  created_at: number;
  updated_at: number;
}

export interface MessagePage {
  items: StoredMessage[];
  last_seq: number;
}

export interface StoredRun {
  run_id: string;
  session_id: string;
  thread_id: string;
  kind: 'find' | 'verify';
  symbol: string;
  timeframe: string;
  profile: string;
  model: string | null;
  manual_trade: Record<string, unknown> | null;
  status: 'running' | 'watching' | 'complete' | 'cancelled' | 'error' | 'truncated';
  terminal_status: string | null;
  started_at: number;
  ended_at: number | null;
  last_seq: number;
}

/** One stored SSE frame, in the shape the reducer already consumes. */
export interface StoredEvent {
  seq: number;
  event: string;
  data: Record<string, unknown>;
}

export interface EventPage {
  items: StoredEvent[];
  last_seq: number;
}

/**
 * A failed session call, carrying the status so callers can branch on 404 vs 401.
 *
 * The distinction is load-bearing for the workspace: 404 means "this session is gone,
 * show the not-found state and offer a new one", whereas 401 means "the session cookie
 * expired, the app shell should re-check auth". Collapsing them into one error would make
 * an expired login look like a deleted conversation.
 */
export class FqApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'FqApiError';
    this.status = status;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      // The live session surface — never serve a cached answer, or an archived session
      // reappears in the tab bar after the user closed it.
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch (err) {
    // A transport failure is not a 4xx, and must not be reported as one — "session not
    // found" for an offline client would send the user to delete and recreate it.
    throw new FqApiError(
      err instanceof Error ? err.message : 'network request failed',
      0,
    );
  }

  if (!res.ok) {
    const detail = await res
      .json()
      .then((body: { detail?: string; error?: string }) => body?.detail ?? body?.error ?? null)
      .catch(() => null);
    throw new FqApiError(detail ?? `request failed with HTTP ${res.status}`, res.status);
  }

  // 204 has no body; every other success here returns JSON.
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface CreateSessionInput {
  symbol: string;
  profile: string;
  timeframe: string;
  title?: string | null;
}

export function createSession(input: CreateSessionInput): Promise<SessionSummary> {
  return request<SessionSummary>('/sessions', {
    method: 'POST',
    body: JSON.stringify({
      symbol: input.symbol,
      profile: input.profile,
      timeframe: input.timeframe,
      ...(input.title ? { title: input.title } : {}),
    }),
  });
}

export function listSessions(
  params: { status?: 'active' | 'archived'; cursor?: string | null; limit?: number; q?: string } = {},
): Promise<SessionListPage> {
  const qs = new URLSearchParams();
  if (params.status) qs.set('status', params.status);
  if (params.cursor) qs.set('cursor', params.cursor);
  if (params.limit) qs.set('limit', String(params.limit));
  if (params.q) qs.set('q', params.q);
  const query = qs.toString();
  return request<SessionListPage>(`/sessions${query ? `?${query}` : ''}`);
}

export function getSession(sessionId: string): Promise<SessionSummary> {
  return request<SessionSummary>(`/sessions/${encodeURIComponent(sessionId)}`);
}

/**
 * Patch a session.
 *
 * ``symbol`` and ``profile`` are absent from the input type on purpose — they are immutable
 * server-side (409), and a field that can only ever fail should not be offered.
 */
export function patchSession(
  sessionId: string,
  patch: { title?: string | null; timeframe?: string; status?: 'active' | 'archived'; active_run_id?: string | null },
): Promise<SessionSummary> {
  return request<SessionSummary>(`/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export function archiveSession(sessionId: string): Promise<SessionSummary> {
  return patchSession(sessionId, { status: 'archived' });
}

export function reopenSession(sessionId: string): Promise<SessionSummary> {
  return patchSession(sessionId, { status: 'active' });
}

export function deleteSession(
  sessionId: string,
  opts: { hard?: boolean } = {},
): Promise<{ session_id: string; status: string; hard: boolean }> {
  const qs = opts.hard ? '?hard=true' : '';
  return request(`/sessions/${encodeURIComponent(sessionId)}${qs}`, { method: 'DELETE' });
}

export function listMessages(
  sessionId: string,
  params: { afterSeq?: number; limit?: number } = {},
): Promise<MessagePage> {
  const qs = new URLSearchParams();
  if (params.afterSeq) qs.set('after_seq', String(params.afterSeq));
  if (params.limit) qs.set('limit', String(params.limit));
  const query = qs.toString();
  return request<MessagePage>(
    `/sessions/${encodeURIComponent(sessionId)}/messages${query ? `?${query}` : ''}`,
  );
}

export function listRuns(sessionId: string): Promise<{ items: StoredRun[] }> {
  return request<{ items: StoredRun[] }>(`/sessions/${encodeURIComponent(sessionId)}/runs`);
}

/**
 * The durable glass-box transcript for a run.
 *
 * Feeding these frames through the same reducer a live stream drives is what makes a
 * reopened session render identically to one that was watched live — the reason the backend
 * stores payloads rather than renderings.
 */
export function listRunEvents(
  runId: string,
  params: { afterSeq?: number; limit?: number } = {},
): Promise<EventPage> {
  const qs = new URLSearchParams();
  if (params.afterSeq) qs.set('after_seq', String(params.afterSeq));
  if (params.limit) qs.set('limit', String(params.limit));
  const query = qs.toString();
  return request<EventPage>(`/runs/${encodeURIComponent(runId)}/events${query ? `?${query}` : ''}`);
}
