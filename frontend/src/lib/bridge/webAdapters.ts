// lib/bridge/webAdapters.ts — the ONE place that knows how each Tauri command is
// served over HTTP in a browser.
//
// Every entry here answers the same question the Rust command answers, using the
// same upstream service, reached through the same-origin `/api/*` proxies in
// `src/app/api/` (which hold the gateway credential server-side). Where a command
// is genuinely meaningless on the web — or already has a first-class browser path
// elsewhere in the codebase — it is listed in `NATIVE_BROWSER_PATH` or
// `NOT_APPLICABLE_ON_WEB` with the reason, rather than silently omitted.
//
// Adding a Tauri command? Add it to exactly one of the three tables below. The
// unit tests assert the union covers `invoke_handler![]` completely, so a new
// command cannot quietly reach a browser as an undefined-`invoke` TypeError.

import { emitBridgeEvent, relaySse } from './events';
import {
  isSafeName,
  liveExpiryClause,
  nearestExpiry,
  pickContract,
  quote,
  selectAtm,
  spotSymbolCandidates,
  underlyingCandidates,
  underlyingClause,
  type ChainRow,
  type ResolvedContract,
} from './fnoWeb';

// ── HTTP helpers ────────────────────────────────────────────────────────────

/**
 * A failed proxy call, carrying the upstream's `{ error }` message.
 *
 * Rust commands reject with a plain `String`, and the UI renders that string
 * (e.g. `useQuantStore.sentimentError`). Throwing an `Error` whose `message` is
 * the server's message keeps that contract byte-for-byte.
 */
async function failure(res: Response, fallback: string): Promise<Error> {
  let message = fallback;
  try {
    const body = await res.json();
    if (body && typeof body.error === 'string' && body.error.trim()) message = body.error;
  } catch {
    /* non-JSON body — keep the fallback */
  }
  return new Error(message);
}

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { cache: 'no-store', ...init });
  if (!res.ok) throw await failure(res, `${path} failed with HTTP ${res.status}`);
  return (await res.json()) as T;
}

/**
 * Rewrite a `/api/kite/*` path onto `NEXT_PUBLIC_KITE_PROXY_ORIGIN` when set —
 * the SAME knob `lib/kiteFetch.ts` uses — so local dev can send Kite REST calls
 * to a deployment that actually reaches the aggregator (e.g.
 * `https://app.stratai.live`). Empty var = same-origin (unchanged).
 *
 * ONLY the `/api/kite/*` route is redirected: it is the one proxy whose response
 * carries `Access-Control-Allow-Origin` (forwarded from the aggregator), so a
 * cross-origin browser fetch to it succeeds. The other `/api/*` proxies
 * (questdb, sentiment, tools, deepquant, features) send no CORS header on the
 * deployed site, so redirecting them cross-origin would be blocked by the
 * browser — they stay same-origin.
 */
const KITE_PROXY_ORIGIN = (process.env.NEXT_PUBLIC_KITE_PROXY_ORIGIN ?? '').replace(/\/+$/, '');

function kiteApiUrl(path: string): string {
  const rel = path.startsWith('/') ? path : `/${path}`;
  return `${KITE_PROXY_ORIGIN}${rel}`;
}

async function apiText(path: string, init?: RequestInit): Promise<string> {
  const res = await fetch(path, { cache: 'no-store', ...init });
  if (!res.ok) throw await failure(res, `${path} failed with HTTP ${res.status}`);
  return res.text();
}

function postJson(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
}

// ── Argument helpers ────────────────────────────────────────────────────────
//
// Tauri deserializes command args into typed Rust params and rejects a mismatch
// before the body runs. These reproduce that guard so a bad call fails with a
// readable message instead of an `undefined` sneaking into a URL.

type Args = Record<string, unknown>;

function reqStr(args: Args, key: string, cmd: string): string {
  const v = args[key];
  if (typeof v !== 'string' || v.trim() === '') {
    throw new Error(`${cmd}: argument "${key}" must be a non-empty string`);
  }
  return v;
}

function optStr(args: Args, key: string): string | undefined {
  const v = args[key];
  return typeof v === 'string' && v.trim() !== '' ? v : undefined;
}

/**
 * Read the thread id, accepting either `thread_id` or `threadId`.
 *
 * These adapters replaced a Tauri IPC layer that converted camelCase JS args to
 * snake_case Rust params automatically. `bridgeInvoke` passes the args object
 * through verbatim, so that conversion silently disappeared — and the callers in
 * `useQuantStore` still send `threadId`. The result was that
 * `cancel_deep_quant_agent` threw on its own argument check before it could
 * abort anything, which is why the STOP button did nothing: the SSE relay kept
 * running and `/cancel` was never sent. `ask_trade_question` had the identical
 * defect, failing every follow-up question.
 *
 * Accepting both spellings here is the same tolerance `startAgentRun` already
 * applies to `fnoExpiry`/`userId`, and it fixes every current and future caller
 * rather than one call site.
 */
function reqThreadId(args: Args, cmd: string): string {
  const v = optStr(args, 'thread_id') ?? optStr(args, 'threadId');
  if (!v) {
    throw new Error(`${cmd}: argument "thread_id" must be a non-empty string`);
  }
  return v;
}

function reqNum(args: Args, key: string, cmd: string): number {
  const v = args[key];
  if (typeof v !== 'number' || !Number.isFinite(v)) {
    throw new Error(`${cmd}: argument "${key}" must be a finite number`);
  }
  return v;
}

// ── Instrument search ───────────────────────────────────────────────────────

/** A row as returned by `aggregator/src/kite_api.rs::instruments_search`. */
interface InstrumentRow {
  tradingsymbol: string;
  name: string;
  exchange: string;
  instrument_type: string;
  expiry?: string;
  strike?: number;
  /** Kite's segment. `INDICES` marks an index; see `EquityResult.segment`. */
  segment?: string;
}

/**
 * The tagged union `commands/instruments.rs::SearchResult` serializes to.
 *
 * Structurally compatible with `types/searchResult.ts`, which is the contract the
 * UI consumes. Keep `segment` on the EQ arm in step with `EquityResult.segment`:
 * this copy silently lacking it is what dropped the field on the way through.
 */
export type SearchResult =
  | { kind: 'EQ'; symbol: string; name: string; exchange: string; segment?: string }
  | {
      kind: 'FNO';
      tradingsymbol: string;
      underlying: string;
      expiry: string;
      strike: number | null;
      optionType: string;
    };

/**
 * Map aggregator instrument rows onto `SearchResult`.
 *
 * Exported for unit testing: this is the only shape translation on the search
 * path, so it is the only place the two transports could disagree.
 */
export function rowsToSearchResults(rows: InstrumentRow[]): SearchResult[] {
  const out: SearchResult[] = [];
  for (const r of rows) {
    const type = (r.instrument_type ?? '').toUpperCase();
    if (type === 'CE' || type === 'PE' || type === 'FUT') {
      out.push({
        kind: 'FNO',
        tradingsymbol: r.tradingsymbol,
        // `nfo_instruments.underlying` is derived from the CSV `name` column on
        // the desktop side too (`instrument_master.rs::derive_underlying`).
        underlying: r.name || r.tradingsymbol,
        expiry: r.expiry ?? '',
        // `SearchResult::Fno.strike` is None for futures and non-positive
        // strikes; mirror that rather than emitting a misleading 0.
        strike: typeof r.strike === 'number' && r.strike > 0 ? r.strike : null,
        optionType: type,
      });
    } else {
      out.push({
        kind: 'EQ',
        symbol: r.tradingsymbol,
        name: r.name,
        exchange: r.exchange,
        segment: r.segment,
      });
    }
  }
  return out;
}

async function searchExchange(query: string, exchange: string): Promise<InstrumentRow[]> {
  try {
    const data = await apiJson<{ results?: InstrumentRow[] }>(
      kiteApiUrl(`/api/kite/instruments?q=${encodeURIComponent(query)}&exchange=${exchange}`),
    );
    return data.results ?? [];
  } catch (err) {
    // One leg failing must not blank the other. `search_in_db` errors only when
    // BOTH tables are missing; this is the transport analogue.
    console.warn(`[bridge] instrument search on ${exchange} failed:`, err);
    return [];
  }
}

// ── Deep-quant agent streaming ──────────────────────────────────────────────
//
// Mirrors `commands/deep_quant.rs`: POST /run, relay every SSE frame onto
// `deep-quant-stream` as `{ event, data }`, and — when the graph parks at a
// price-watch interrupt (`RUN_FINISHED` with `status: "paused"`) — reattach to
// the per-thread fan-out hub so server-initiated resumes keep flowing.

/** thread_id → abort handle, so `cancel_deep_quant_agent` can stop the relay. */
const activeRuns = new Map<string, AbortController>();

/** Structural check standing in for Rust's `from_value::<ConsensusReport>`. */
function looksLikeConsensus(v: unknown): boolean {
  if (!v || typeof v !== 'object') return false;
  const o = v as Record<string, unknown>;
  return typeof o.symbol === 'string' && typeof o.trend_score === 'number';
}

/**
 * Re-emit an agent consensus frame onto `quant-consensus`.
 *
 * The technical HUD is fed by that event. In the thin-client topology the agent
 * runs server-side against the headless tool-server, which has no AppHandle, so
 * nothing would ever reach the UI — `relay_deep_quant_sse` solves this on
 * desktop by re-emitting the tool result, and this is the browser twin.
 */
function bridgeConsensusFrame(frame: { event: string; data: unknown }): void {
  if (frame.event !== 'TOOL_CALL_RESULT') return;
  const d = frame.data as Record<string, unknown> | null;
  if (!d || d.tool !== 'get_consensus_report' || d.summarized !== undefined) return;
  if (looksLikeConsensus(d.result)) emitBridgeEvent('quant-consensus', d.result);
}

/** Consume one SSE response onto `deep-quant-stream`; report how it ended. */
async function relayAgentStream(
  res: Response,
  signal: AbortSignal,
): Promise<'paused' | 'completed' | 'errored' | 'disconnected'> {
  if (!res.body) return 'errored';
  let outcome: 'paused' | 'completed' | 'errored' | 'disconnected' = 'disconnected';

  await relaySse(
    res.body,
    (frame) => {
      if (frame.event === 'RUN_FINISHED') {
        const status = (frame.data as Record<string, unknown> | null)?.status;
        outcome = status === 'paused' ? 'paused' : 'completed';
      } else if (frame.event === 'ERROR') {
        outcome = 'errored';
      }
      bridgeConsensusFrame(frame);
      emitBridgeEvent('deep-quant-stream', { event: frame.event, data: frame.data });
    },
    signal,
  );

  return outcome;
}

/** Pre-run consensus, matching `run_deep_quant_agent`'s emit before POST /run. */
async function emitPreRunConsensus(symbol: string, timeframe: string): Promise<void> {
  try {
    const report = await apiJson<unknown>(
      '/api/tools/get_consensus',
      postJson({ symbol, timeframe, limit: 200 }),
    );
    if (looksLikeConsensus(report)) emitBridgeEvent('quant-consensus', report);
  } catch (err) {
    // Non-fatal on desktop too — the run proceeds, the HUD just stays as-is.
    console.warn('[bridge] pre-run consensus unavailable:', err);
  }
}

/**
 * Start a SESSION-scoped agent run.
 *
 * Returns the `session_id` rather than a thread id, because the thread no longer exists at
 * call time: the server mints it inside `POST /run` and reports it on the `RUN_STARTED`
 * frame, which `useSessionStore.applyFrame` uses to bind the thread to this session.
 *
 * That inversion is the point. The client used to mint
 * `thread_${symbol}_${Date.now()}` — guessable to the second — and `GET /stream/{thread_id}`
 * had no ownership check, so knowing a symbol and roughly when someone ran it was enough to
 * read their research stream.
 *
 * The reattach loop now passes `?after_seq=`, which closes the window where frames published
 * while nobody was subscribed were lost with no way to recover them. That window is exactly
 * when a paused run's client is reconnecting, so it was not theoretical.
 */
async function startSessionRun(args: Args): Promise<string> {
  const sessionId = reqStr(args, 'session_id', 'run_deep_quant_agent');
  const mode = optStr(args, 'mode') ?? 'FIND';
  const manualTrade = (args.manual_trade ?? args.manualTrade) as Record<string, unknown> | undefined;
  const symbol = optStr(args, 'symbol') ?? '';

  const message = buildRunMessage(symbol, mode, manualTrade);

  const payload = {
    session_id: sessionId,
    message,
    mode,
    // Still sent because the GRAPH needs them — a VERIFY of specific numbers must not
    // change under the user. The server records the SESSION's context on the run row, so
    // these cannot rewrite what an earlier run claims to have analysed.
    symbol: symbol || null,
    timeframe: optStr(args, 'timeframe') ?? null,
    profile: optStr(args, 'profile') ?? null,
    fno_expiry: optStr(args, 'fno_expiry') ?? optStr(args, 'fnoExpiry') ?? null,
    model: optStr(args, 'model') ?? null,
    manual_trade: manualTrade ?? null,
    client_msg_id: optStr(args, 'client_msg_id') ?? optStr(args, 'clientMsgId') ?? null,
  };

  const controller = new AbortController();
  activeRuns.set(sessionId, controller);

  void emitPreRunConsensus(symbol, optStr(args, 'timeframe') ?? '10m');

  // Deliberately not awaited: the caller transitions into its streaming state immediately,
  // as it did before.
  void (async () => {
    try {
      const res = await fetch('/api/deepquant/run', {
        ...postJson(payload),
        signal: controller.signal,
      });
      if (!res.ok) throw await failure(res, `deep-quant /run failed with HTTP ${res.status}`);

      let outcome = await relayAgentStream(res, controller.signal);

      while (outcome === 'paused' && !controller.signal.aborted) {
        // The thread and the last seq are only known once RUN_STARTED has been routed, so
        // they are read at reattach time rather than captured up front.
        const { useSessionStore } = await import('../../store/useSessionStore');
        const stream = useSessionStore.getState().streams[sessionId];
        const threadId = stream?.threadId;
        if (!threadId) break;

        const qs = stream.lastSeq > 0 ? `?after_seq=${stream.lastSeq}` : '';
        const hub = await fetch(
          `/api/deepquant/stream/${encodeURIComponent(threadId)}${qs}`,
          { signal: controller.signal, cache: 'no-store' },
        );
        if (!hub.ok) break;
        outcome = await relayAgentStream(hub, controller.signal);
        // A clean disconnect while still paused means the hub connection dropped, not that
        // the graph finished — reattach.
        if (outcome === 'disconnected') outcome = 'paused';
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      emitBridgeEvent('deep-quant-stream', {
        event: 'ERROR',
        data: { error: err instanceof Error ? err.message : String(err) },
      });
    } finally {
      activeRuns.delete(sessionId);
    }
  })();

  return sessionId;
}

/** The prompt text for a run. Extracted so both run paths build it identically. */
function buildRunMessage(
  symbol: string,
  mode: string,
  manualTrade: Record<string, unknown> | undefined,
): string {
  return mode === 'VERIFY' && manualTrade
    ? `Verify the following proposed trade setup for the trading ticker symbol '${symbol}':\n` +
        `- Side: ${manualTrade.side}\n` +
        `- Entry Price: ${manualTrade.entry}\n` +
        `- Stop Loss: ${manualTrade.stop_loss ?? manualTrade.stopLoss}\n` +
        `- Target/Take Profit: ${manualTrade.take_profit ?? manualTrade.takeProfit}\n` +
        `- My Trade Logic/Analysis: '${manualTrade.user_analysis ?? manualTrade.userAnalysis}'\n` +
        `Please evaluate this setup against recent candlestick data and technical consensus, ` +
        `validate the risk-reward profile, and recommend whether to execute, adjust, or reject the trade.`
    : `Analyze the trading ticker symbol '${symbol}' and recommend a setup.`;
}

/** Start an agent run and stream it. Returns the thread id immediately. */
async function startAgentRun(args: Args): Promise<string> {
  // Session-scoped runs take the path above. Dispatched on the ARGUMENT rather than on the
  // build flag, so the two paths cannot disagree with the caller about which one ran.
  if (optStr(args, 'session_id')) return startSessionRun(args);

  const symbol = reqStr(args, 'symbol', 'run_deep_quant_agent');
  const mode = optStr(args, 'mode') ?? 'FIND';
  const profile = optStr(args, 'profile') ?? 'INTRADAY';
  const timeframe = optStr(args, 'timeframe');
  const manualTrade = (args.manual_trade ?? args.manualTrade) as Record<string, unknown> | undefined;

  // Same id format as the Rust command, so persisted Python threads look alike.
  const threadId = `thread_${symbol}_${Date.now()}`;

  // Fire-and-forget, deliberately NOT awaited.
  //
  // This is a non-streaming proxy call bounded by the 30s PROXY_TIMEOUT_MS. When
  // the tool-server is slow or unreachable, awaiting it meant `POST /run` was not
  // even issued for up to 30 seconds after the user pressed "Find Quant Trade" —
  // the press looked like it did nothing. The consensus only populates the
  // technical HUD; the agent stream is the primary result and must not wait on
  // it. `emitPreRunConsensus` already swallows its own failures.
  void emitPreRunConsensus(symbol, timeframe ?? '10m');

  const message =
    mode === 'VERIFY' && manualTrade
      ? `Verify the following proposed trade setup for the trading ticker symbol '${symbol}':\n` +
        `- Side: ${manualTrade.side}\n` +
        `- Entry Price: ${manualTrade.entry}\n` +
        `- Stop Loss: ${manualTrade.stop_loss ?? manualTrade.stopLoss}\n` +
        `- Target/Take Profit: ${manualTrade.take_profit ?? manualTrade.takeProfit}\n` +
        `- My Trade Logic/Analysis: '${manualTrade.user_analysis ?? manualTrade.userAnalysis}'\n` +
        `Please evaluate this setup against recent candlestick data and technical consensus, ` +
        `validate the risk-reward profile, and recommend whether to execute, adjust, or reject the trade.`
      : `Analyze the trading ticker symbol '${symbol}' and recommend a setup.`;

  const payload = {
    thread_id: threadId,
    message,
    mode,
    symbol,
    timeframe: timeframe ?? null,
    profile,
    fno_expiry: optStr(args, 'fno_expiry') ?? optStr(args, 'fnoExpiry') ?? null,
    model: optStr(args, 'model') ?? null,
    manual_trade: manualTrade ?? null,
    user_id: optStr(args, 'user_id') ?? optStr(args, 'userId') ?? null,
  };

  const controller = new AbortController();
  activeRuns.set(threadId, controller);

  // Deliberately not awaited: the Rust command spawns the relay and returns the
  // thread id straight away so the UI can transition into its streaming state.
  void (async () => {
    try {
      const res = await fetch('/api/deepquant/run', {
        ...postJson(payload),
        signal: controller.signal,
      });
      if (!res.ok) throw await failure(res, `deep-quant /run failed with HTTP ${res.status}`);

      let outcome = await relayAgentStream(res, controller.signal);

      while (outcome === 'paused' && !controller.signal.aborted) {
        const hub = await fetch(`/api/deepquant/stream/${encodeURIComponent(threadId)}`, {
          signal: controller.signal,
          cache: 'no-store',
        });
        if (!hub.ok) break;
        outcome = await relayAgentStream(hub, controller.signal);
        // A clean disconnect while still paused means the hub connection
        // dropped, not that the graph finished — reattach.
        if (outcome === 'disconnected') outcome = 'paused';
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      emitBridgeEvent('deep-quant-stream', {
        event: 'ERROR',
        data: { error: err instanceof Error ? err.message : String(err) },
      });
    } finally {
      activeRuns.delete(threadId);
    }
  })();

  return threadId;
}

// ── Workspace + radar local stores ──────────────────────────────────────────
//
// `save_workspace`/`load_workspace` persist to the desktop SQLite workspace DB
// and `set_radar_symbols` to an in-process registry. The browser equivalent is
// `localStorage`: same per-user, per-device scope, and it survives a reload,
// which the current browser fallback (`charting/workspace.ts` in-memory) does not.
//
// `readLocal`/`writeLocal` below are SHARED by both of those features. They also
// used to back a simulated paper-trading portfolio, which has been removed.

const WORKSPACE_KEY = (symbol: string) => `stratai.workspace.${symbol}`;
const RADAR_KEY = 'stratai.radar.symbols';
function readLocal(key: string): string | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage.getItem(key);
  } catch {
    return null; // private mode / storage disabled
  }
}

/**
 * Write to `localStorage`, THROWING when the write could not happen.
 *
 * Deliberately not best-effort: the desktop `save_workspace` returns
 * `Result<_, String>` and `charting/workspace.ts` uses a failed save to decide
 * whether to keep the state in its in-memory session store and report
 * `flushWorkspace() === false`. Swallowing a quota error here would make that
 * layer claim a durable save that never happened.
 */
function writeLocal(key: string, value: string): void {
  if (typeof localStorage === 'undefined') {
    throw new Error('localStorage is unavailable in this environment');
  }
  localStorage.setItem(key, value); // quota / private-mode errors propagate
}

/** Mirrors `quant::radar::RadarRegistry::set_symbols` — trim, upper, dedupe. */export function cleanRadarSymbols(input: unknown): string[] {
  if (!Array.isArray(input)) return [];
  const cleaned: string[] = [];
  for (const raw of input) {
    if (typeof raw !== 'string') continue;
    const up = raw.trim().toUpperCase();
    if (up && !cleaned.includes(up)) cleaned.push(up);
  }
  return cleaned;
}

// ── QuestDB / F&O chain helpers ──────────────────────────────────────────────

/**
 * Run a statement through the credential-holding proxy and return QuestDB's
 * `dataset` rows.
 *
 * QuestDB's REST `/exec` answers `{ dataset: [[...], ...] }` positionally, with no
 * column names on each row — so callers destructure in SELECT order. An error is
 * reported in-band as `{ error }` with HTTP 200, which is why that is checked here
 * rather than relying on the status code.
 */
async function questdbRows(query: string): Promise<unknown[][]> {
  const body = await apiJson<{ dataset?: unknown[][]; error?: string }>(
    `/api/questdb/exec?query=${encodeURIComponent(query)}&fmt=json`,
  );
  if (body.error) throw new Error(`QuestDB: ${body.error}`);
  return body.dataset ?? [];
}

/** The nearest non-expired expiry with snapshots, or null when there are none. */
async function nearestExpiryFor(underlying: string): Promise<string | null> {
  const rows = await questdbRows(
    `SELECT DISTINCT expiry FROM option_chain_snapshots ` +
      `WHERE ${underlyingClause(underlying)} AND ${liveExpiryClause()}`,
  );
  return nearestExpiry(rows.map(([e]) => String(e)));
}

/**
 * The latest snapshot's tradable contracts for one underlying/expiry.
 *
 * Pinned to `max(snapshot_ts)` exactly as `fno_service::fetch_snapshots_from_questdb`
 * does — without it the result mixes strikes from different ingestion ticks, and the
 * OI comparison in `pickContract` would weigh readings taken minutes apart.
 */
async function chainRows(underlying: string, expiry: string): Promise<ChainRow[]> {
  if (!isSafeName(expiry)) throw new Error(`unsafe expiry: ${expiry}`);
  const where = `${underlyingClause(underlying)} AND expiry = ${quote(expiry)}`;
  const rows = await questdbRows(
    `SELECT strike, option_type, symbol, open_interest FROM option_chain_snapshots ` +
      `WHERE ${where} AND snapshot_ts = (SELECT max(snapshot_ts) FROM option_chain_snapshots WHERE ${where}) ` +
      `ORDER BY strike ASC`,
  );
  return rows.flatMap(([strike, optionType, symbol, oi]) => {
    const s = Number(strike);
    const t = String(optionType);
    if (!Number.isFinite(s) || !symbol || (t !== 'CE' && t !== 'PE')) return [];
    return [{ strike: s, optionType: t, symbol: String(symbol), oi: Number(oi) || 0 }];
  });
}

/** Shared body of `fno_resolve_nearest_contract`: nearest expiry → ATM → contract. */
async function resolveContract(
  underlying: string,
  expiryArg?: string,
): Promise<ResolvedContract | null> {
  const expiry = expiryArg ?? (await nearestExpiryFor(underlying));
  if (!expiry) return null;

  const rows = await chainRows(underlying, expiry);
  if (rows.length === 0) return null;

  const strikes = [...new Set(rows.map((r) => r.strike))].sort((a, b) => a - b);
  const spot = await readSpot(underlying);
  // No spot ⇒ the median listed strike, matching the Rust fallback. A chain is
  // built around ATM, so its median is a reasonable stand-in.
  const atm =
    spot !== null ? selectAtm(strikes, spot) ?? strikes[strikes.length >> 1] : strikes[strikes.length >> 1];

  const picked = pickContract(rows, atm);
  return picked
    ? {
        tradingsymbol: picked.symbol,
        underlying,
        expiry,
        strike: picked.strike,
        option_type: picked.optionType,
      }
    : null;
}

/** Latest spot from `live_ticks`, trying each symbol-name variant. */
async function readSpot(underlying: string): Promise<number | null> {
  const names = spotSymbolCandidates(underlying).filter(isSafeName);
  if (names.length === 0) return null;
  const rows = await questdbRows(
    `SELECT last_traded_price FROM live_ticks WHERE symbol IN (${names.map(quote).join(',')}) ` +
      `ORDER BY timestamp DESC LIMIT 1`,
  );
  const price = Number(rows[0]?.[0]);
  return Number.isFinite(price) && price > 0 ? price : null;
}

// ── The registry ────────────────────────────────────────────────────────────

export type WebAdapter = (args: Args) => Promise<unknown>;

export const WEB_ADAPTERS: Record<string, WebAdapter> = {
  // ── Market data ───────────────────────────────────────────────────────────
  search_instruments: async (args) => {
    const query = (args.query as string | undefined)?.trim() ?? '';
    if (!query) return [];
    // `search_in_db` queries `instruments` then `nfo_instruments` and returns
    // equities first; parallel exchange calls reproduce that ordering.
    //
    // BSE is searched as well as NSE, which it previously was not — and that is
    // why SENSEX could never be found. SENSEX is a BSE index (segment `INDICES`,
    // token 265), as are BANKEX and 71 others; searching "SENSEX" against NSE
    // alone returns only the ETFs that track it (`SENSEXETF`, `SENSEXBEES`,
    // `HDFCSENSEX`…) and never the index itself. NSE stays first so its listings
    // outrank the BSE duplicate for dually-listed scrips.
    //
    // BFO is the derivative half of the same split: SENSEX and BANKEX contracts
    // (`SENSEX2690376900CE`, segment `BFO-OPT`) exist only there, so an NFO-only
    // search could find no SENSEX option at all.
    const [nse, bse, fno, bfo] = await Promise.all([
      searchExchange(query, 'NSE'),
      searchExchange(query, 'BSE'),
      searchExchange(query, 'NFO'),
      searchExchange(query, 'BFO'),
    ]);
    return rowsToSearchResults([...nse, ...bse, ...fno, ...bfo]);
  },

  fetch_questdb: async (args) => {
    const query = reqStr(args, 'query', 'fetch_questdb');
    return apiText(`/api/questdb/exec?query=${encodeURIComponent(query)}&fmt=json`);
  },

  get_pool_status: async () => {
    // The desktop answer is "is the PG pool registered?". The web analogue is
    // "does QuestDB answer?", which is the property every caller actually wants.
    try {
      const res = await fetch('/api/questdb/exec?query=select%201&fmt=json', { cache: 'no-store' });
      return res.ok;
    } catch {
      return false;
    }
  },

  // Live ticks reach the browser over the open `/ws/*` gateway prefix, connected
  // directly by `useTradeStore.connectAlphaWebSocket` and friends, so there is
  // nothing for a per-symbol call to do CLIENT-side.
  //
  // ⚠ Do NOT read this as "tick subscription is unnecessary". An earlier version of
  // this comment claimed the WS feeds are symbol-agnostic; they are not. The
  // ingestion service boots with an empty instrument map and streams only the
  // tokens pushed to its control port, so SOMETHING has to subscribe them. The
  // desktop app used to, via this command; that work now lives server-side in
  // `aggregator/src/spot_subscriber.rs`, which resolves the configured symbols and
  // re-asserts them on a timer. Removing it makes the tick feed go quiet while
  // every health check stays green.
  subscribe_ticker: async () => undefined,

  // ── Deployment configuration ──────────────────────────────────────────────
  // The feature kill switches are resolved by the server (`/api/features`) and
  // by the Rust shell on desktop, never by a value baked into this bundle. See
  // `app/api/_featureSwitches.ts` for the reasoning.
  get_feature_switches: async () => apiJson('/api/features'),

  // ── Sentiment ─────────────────────────────────────────────────────────────
  fetch_symbol_sentiment: async (args) => {
    const symbol = reqStr(args, 'symbol', 'fetch_symbol_sentiment');
    return apiJson(`/api/sentiment?symbol=${encodeURIComponent(symbol)}`);
  },

  // ── Deep-quant agent ──────────────────────────────────────────────────────
  run_deep_quant_agent: async (args) => startAgentRun(args),

  run_ai_analysis: async (args) => {
    // Desktop runs a separate local glass-box loop here; on the web the agent
    // service is the only executor, so both entry points drive the same run.
    await startAgentRun({ ...args, timeframe: optStr(args, 'timeframe') ?? '10m' });
    return undefined;
  },

  run_deep_quant_analysis: async (args) => {
    await startAgentRun({ ...args, mode: 'FIND' });
    return undefined;
  },

  cancel_deep_quant_agent: async (args) => {
    // `run_id` is preferred: it is the identifier this service minted, so the server can
    // check ownership on it directly. `/cancel` previously took no user id at all, which
    // meant any caller who knew a thread id could stop somebody else's analysis.
    const runId = optStr(args, 'run_id') ?? optStr(args, 'runId');
    const sessionId = optStr(args, 'session_id') ?? optStr(args, 'sessionId');
    const threadId = optStr(args, 'thread_id') ?? optStr(args, 'threadId');

    // Abort the local relay first, under whichever key started it, so the UI stops
    // immediately even if the server call fails.
    for (const key of [sessionId, threadId, runId]) {
      if (!key) continue;
      activeRuns.get(key)?.abort();
      activeRuns.delete(key);
    }

    if (!runId && !threadId) {
      // A cancel pressed before RUN_STARTED has been routed has nothing to name yet. The
      // local abort above is the whole stop in that case, and it is honest to say so
      // rather than POST an identifier we do not have.
      return undefined;
    }

    // Ask Python to break out of astream at the next step boundary. NOT swallowed: if this
    // fails the run is still burning LLM credits server-side while the UI shows it stopped,
    // which the caller needs to know about.
    const res = await fetch(
      '/api/deepquant/cancel',
      postJson(runId ? { run_id: runId } : { thread_id: threadId }),
    );
    if (!res.ok) {
      throw await failure(res, `deep-quant /cancel failed with HTTP ${res.status}`);
    }
    return undefined;
  },

  ask_trade_question: async (args) => {
    const question = reqStr(args, 'question', 'ask_trade_question');
    const sessionId = optStr(args, 'session_id') ?? optStr(args, 'sessionId');
    // Grounding is NAMED, not inferred. The old client read its thread id from a flat
    // "current" store field — i.e. whatever session was on screen — so switching tabs
    // mid-question asked about the wrong analysis. `context_run_id` also makes multiple
    // FIND runs per session usable: without it, "why that stop?" after a second run could
    // only ever mean the second one.
    const payload = sessionId
      ? {
          session_id: sessionId,
          context_run_id: optStr(args, 'context_run_id') ?? optStr(args, 'contextRunId') ?? null,
          question,
          model: optStr(args, 'model') ?? null,
          client_msg_id: optStr(args, 'client_msg_id') ?? optStr(args, 'clientMsgId') ?? null,
        }
      : {
          thread_id: reqThreadId(args, 'ask_trade_question'),
          question,
          model: optStr(args, 'model') ?? null,
          user_id: optStr(args, 'user_id') ?? optStr(args, 'userId') ?? null,
        };
    // A session id is NOT a thread id.
    //
    // This used to be `sessionId ?? payload.thread_id`, which stamped the session id into the
    // `thread_id` field of the synthetic terminal below. The store routes strictly
    // `thread_id → session_id`, so that frame could never be routed and the composer stayed
    // locked forever — with no further frame able to unlock it. On the session path the thread
    // is not known at call time at all: the server mints it and reports it on `RUN_STARTED`,
    // so it is LEARNED from the stream.
    const legacyThreadId = sessionId ? null : (payload as { thread_id: string }).thread_id;
    let observedThreadId: string | null = legacyThreadId;

    /**
     * Routing keys for a frame this client synthesizes rather than receives.
     *
     * The server stamps `thread_id` and `turn` on everything it sends (see `_stamp` in
     * `main.py`); a locally built frame has to carry the same keys or it is either unroutable
     * or misrouted into the glass box as if it were analysis reasoning.
     */
    const localKeys = () => ({
      ...(observedThreadId ? { thread_id: observedThreadId } : {}),
      // Names the session directly, so a synthetic frame is routable even when no thread was
      // ever observed — which is exactly the case when the request failed outright.
      ...(sessionId ? { session_id: sessionId } : {}),
      turn: 'qa',
    });

    void (async () => {
      let sawRunFinished = false;
      try {
        const res = await fetch('/api/deepquant/qa', postJson(payload));
        if (!res.ok || !res.body) {
          throw await failure(res, `deep-quant /qa failed with HTTP ${res.status}`);
        }
        await relaySse(res.body, (frame) => {
          if (frame.event === 'RUN_FINISHED') sawRunFinished = true;
          // Learn the real thread id from the stream so a synthetic terminal can be routed the
          // same way the server's own frames were.
          const carried = (frame.data as { thread_id?: unknown } | undefined)?.thread_id;
          if (typeof carried === 'string' && carried) observedThreadId = carried;
          emitBridgeEvent('deep-quant-qa-stream', { event: frame.event, data: frame.data });
        });
      } catch (err) {
        emitBridgeEvent('deep-quant-qa-stream', {
          event: 'ERROR',
          data: { ...localKeys(), error: err instanceof Error ? err.message : String(err) },
        });
        return;
      }
      // Synthetic completion so the UI always leaves its streaming state, as
      // `ask_trade_question` does when the stream ends without RUN_FINISHED.
      if (!sawRunFinished) {
        emitBridgeEvent('deep-quant-qa-stream', {
          event: 'RUN_FINISHED',
          data: { ...localKeys(), status: 'completed' },
        });
      }
    })();

    return undefined;
  },

  // ── F&O ───────────────────────────────────────────────────────────────────
  // `get_fno_analytics` computes the snapshot locally on desktop
  // (`fno_service::build_fno_snapshot`); the Python service exposes the same
  // analytics over the documented F4 transport seam, including the
  // `unavailable` / `reason_code` markers `fno/viewModel.ts` already consumes.
  get_fno_analytics: async (args) => {
    const underlying = reqStr(args, 'underlying', 'get_fno_analytics');
    const expiry = (args.expiry as string | undefined) ?? '';
    const qs = new URLSearchParams({ underlying });
    if (expiry) qs.set('expiry', expiry);
    return apiJson(`/api/deepquant/options/snapshot?${qs.toString()}`);
  },

  // The periodic chain ingestion runs server-side in `option_chain_subscriber.rs`
  // for every deployment, so a browser has nothing to start or stop. The UI
  // re-reads the snapshot on its own cadence.
  fno_subscribe: async () => undefined,
  fno_unsubscribe: async () => undefined,

  // The remaining `fno_*` commands are chain *lookups*. On desktop they join the
  // SQLite NFO master against QuestDB; in a browser one query over
  // `option_chain_snapshots` answers all of them, because that table already
  // carries the real tradingsymbol. See `fnoWeb.ts` for why, and for the one
  // behavioural difference (snapshotted strikes only).
  // Every query below is filtered to non-expired series. `option_chain_snapshots`
  // has no retention job, so it still holds rows for expiries that lapsed weeks
  // ago; unfiltered, those rows made dead underlyings look tradable, and the
  // contract they resolved to had already been dropped from Kite's instrument
  // master — so the chart could never load a single candle for it.
  fno_list_chains: async () => {
    const rows = await questdbRows(
      `SELECT DISTINCT underlying, expiry FROM option_chain_snapshots ` +
        `WHERE ${liveExpiryClause()} ORDER BY underlying, expiry`,
    );
    // Group under one canonical name per underlying, so `NIFTY` and `NIFTY 50`
    // rows do not present as two separate selector entries.
    const grouped = new Map<string, Set<string>>();
    for (const [rawUnderlying, expiry] of rows) {
      const canonical = underlyingCandidates(String(rawUnderlying))[0];
      const set = grouped.get(canonical) ?? new Set<string>();
      if (expiry) set.add(String(expiry));
      grouped.set(canonical, set);
    }
    const underlyings = [...grouped.keys()].sort();
    const expiries_by_underlying: Record<string, string[]> = {};
    for (const u of underlyings) {
      expiries_by_underlying[u] = [...(grouped.get(u) ?? [])].sort();
    }
    return { underlyings, expiries_by_underlying };
  },

  fno_list_expiries: async (args) => {
    const underlying = optStr(args, 'underlying');
    if (!underlying) return []; // Rust returns an empty list, not an error.
    const rows = await questdbRows(
      `SELECT DISTINCT expiry FROM option_chain_snapshots ` +
        `WHERE ${underlyingClause(underlying)} AND ${liveExpiryClause()} ORDER BY expiry ASC`,
    );
    return rows.map(([e]) => String(e)).filter(Boolean);
  },

  // Desktop registers the underlying with the ingester and answers "does this have
  // a chain?". The ingester is server-side, so the browser answers the same
  // question by looking: a snapshot exists ⇒ the chain is being ingested. Callers
  // use the boolean to decide whether to open F&O or fall back to the price chart.
  fno_request_underlying: async (args) => {
    const underlying = optStr(args, 'underlying');
    if (!underlying) return false;
    const rows = await questdbRows(
      `SELECT count() FROM option_chain_snapshots ` +
        `WHERE ${underlyingClause(underlying)} AND ${liveExpiryClause()}`,
    );
    return Number(rows[0]?.[0] ?? 0) > 0;
  },

  /**
   * Is `symbol` a currently-listed option contract?
   *
   * Answers "can this be charted at all", which `isFnoSymbol` cannot: that is a
   * shape test (ends in CE/PE, contains a digit) and it happily accepts a symbol
   * that no exchange ever listed. Two kinds of unchartable symbol reach the chart
   * and look identical to a valid one:
   *
   *   · a FABRICATED short symbol — `BANKNIFTY57000CE`, missing the expiry
   *     segment a real NFO symbol carries. The ladder used to write these, and
   *     because `selectedSymbol` is persisted to preferences, one saved before
   *     that was fixed is restored on every load and keeps the chart empty.
   *     Measured: `BANKNIFTY26SEP57000CE` returns 234 candles, the fabricated
   *     `BANKNIFTY57000CE` returns 0.
   *   · an EXPIRED contract — a persisted `BANKNIFTY26AUG57000CE` after 25 Aug.
   *     Kite drops expired contracts from the instrument master, so it has no
   *     candles and never will again.
   *
   * Filtered to live expiries, so an expired contract correctly answers false.
   * Returns false rather than throwing for a malformed argument: the caller uses
   * this to decide whether to REPAIR the charted symbol, and an error there
   * should not be mistaken for "listed".
   */
  fno_symbol_is_listed: async (args) => {
    const symbol = optStr(args, 'symbol');
    if (!symbol || !isSafeName(symbol)) return false;
    const rows = await questdbRows(
      `SELECT count() FROM option_chain_snapshots ` +
        `WHERE symbol = ${quote(symbol.trim().toUpperCase())} AND ${liveExpiryClause()}`,
    );
    return Number(rows[0]?.[0] ?? 0) > 0;
  },

  fno_resolve_nearest_contract: async (args) => {
    const underlying = reqStr(args, 'underlying', 'fno_resolve_nearest_contract');
    return resolveContract(underlying, optStr(args, 'expiry'));
  },

  fno_resolve_option_contract: async (args) => {
    const underlying = optStr(args, 'underlying');
    const optionType = optStr(args, 'optionType') ?? optStr(args, 'option_type');
    const strike = args.strike;
    // Mirrors the Rust guard: a bad argument is `None`, not an error, because the
    // callers (`useFnoExpiryChange`, `FnoOptionChainTable`) treat null as "leave
    // the chart alone" and would otherwise surface a spurious failure.
    if (
      !underlying ||
      (optionType !== 'CE' && optionType !== 'PE') ||
      typeof strike !== 'number' ||
      !Number.isFinite(strike)
    ) {
      return null;
    }

    const expiry = optStr(args, 'expiry') ?? (await nearestExpiryFor(underlying));
    if (!expiry) return null;

    const rows = await chainRows(underlying, expiry);
    const exact = rows.find((r) => r.strike === strike && r.optionType === optionType);
    if (exact) {
      return {
        tradingsymbol: exact.symbol,
        underlying,
        expiry,
        strike: exact.strike,
        option_type: exact.optionType,
      } satisfies ResolvedContract;
    }
    // Not listed at that exact strike — fall back to the ATM walk rather than
    // returning nothing, matching the Rust command's ±2-strike widening.
    const atm = selectAtm(
      rows.map((r) => r.strike),
      strike,
    );
    if (atm === null) return null;
    const picked = pickContract(rows, atm);
    return picked
      ? ({
          tradingsymbol: picked.symbol,
          underlying,
          expiry,
          strike: picked.strike,
          option_type: picked.optionType,
        } satisfies ResolvedContract)
      : null;
  },

  // ── Radar & patterns ──────────────────────────────────────────────────────
  // All three call quant-core through tool-server rather than reimplementing the
  // detection math in TS — a fork would let the desktop and web surfaces disagree
  // about what the same chart shows.
  scan_radar_symbol: async (args) =>
    apiJson(
      '/api/tools/scan_radar',
      postJson({
        symbol: reqStr(args, 'symbol', 'scan_radar_symbol'),
        timeframe: reqStr(args, 'timeframe', 'scan_radar_symbol'),
        lookback: typeof args.lookback === 'number' ? args.lookback : undefined,
      }),
    ),

  scan_quant_radar: async (args) =>
    apiJson(
      '/api/tools/scan_in_memory',
      postJson({
        symbol: reqStr(args, 'symbol', 'scan_quant_radar'),
        timeframe: reqStr(args, 'timeframe', 'scan_quant_radar'),
        candles: Array.isArray(args.candles) ? args.candles : [],
        lookback: typeof args.lookback === 'number' ? args.lookback : undefined,
      }),
    ),

  get_multi_timeframe_chart_patterns: async (args) =>
    apiJson(
      '/api/tools/get_multi_tf_chart_patterns',
      postJson({ symbol: reqStr(args, 'symbol', 'get_multi_timeframe_chart_patterns') }),
    ),

  /**
   * The consensus report — trend score, momentum/volatility/volume state, and the
   * active candlestick patterns and strategies the HUD renders.
   *
   * Driven by the `FIND QUANT TRADE` press, not by symbol selection. Previously
   * `consensusData` arrived ONLY as a side effect of a deep-quant run relaying its
   * `get_consensus_report` tool result onto the bridge bus, so a run that failed
   * before reaching that tool left the panel reading "No patterns detected" with
   * nothing actually wrong. This is the same `quant-core` ConsensusEngine, reached
   * directly for the same press.
   */
  get_consensus: async (args) =>
    apiJson(
      '/api/tools/get_consensus',
      postJson({
        symbol: reqStr(args, 'symbol', 'get_consensus'),
        timeframe: optStr(args, 'timeframe') ?? '10m',
      }),
    ),

  // ── Misc ──────────────────────────────────────────────────────────────────

  open_browser: async (args) => {
    const url = reqStr(args, 'url', 'open_browser');
    window.open(url, '_blank', 'noopener,noreferrer');
    return undefined;
  },

  save_workspace: async (args) => {
    const symbol = reqStr(args, 'symbol', 'save_workspace');
    const stateJson = (args.stateJson ?? args.state_json) as string | undefined;
    if (typeof stateJson !== 'string') {
      throw new Error('save_workspace: argument "stateJson" must be a string');
    }
    writeLocal(WORKSPACE_KEY(symbol), stateJson);
    return undefined;
  },

  load_workspace: async (args) => {
    const symbol = reqStr(args, 'symbol', 'load_workspace');
    // `db::load_workspace` returns "{}" for a missing row, not an error.
    return readLocal(WORKSPACE_KEY(symbol)) ?? '{}';
  },

  set_radar_symbols: async (args) => {
    const cleaned = cleanRadarSymbols(args.symbols);
    writeLocal(RADAR_KEY, JSON.stringify(cleaned));
    return cleaned;
  },

  get_radar_symbols: async () => {
    const raw = readLocal(RADAR_KEY);
    if (!raw) return [];
    try {
      return cleanRadarSymbols(JSON.parse(raw));
    } catch {
      return [];
    }
  },

  // `lib/tauriFetch.ts` already has its own browser branch for both of these, so
  // these adapters exist for completeness (and for any future caller that goes
  // through the bridge instead).
  kite_fetch: async (args) => {
    const path = reqStr(args, 'path', 'kite_fetch');
    const rel = path.startsWith('/') ? path : `/${path}`;
    const res = await fetch(kiteApiUrl(`/api/kite${rel}`), { cache: 'no-store' });
    return { status: res.status, ok: res.ok, body: await res.text() };
  },

  api_fetch: async (args) => {
    const url = reqStr(args, 'url', 'api_fetch');
    const method = optStr(args, 'method') ?? 'GET';
    const headers = (args.headers as Record<string, string> | undefined) ?? undefined;
    const body = optStr(args, 'body');
    const res = await fetch(url, { method, headers, body, cache: 'no-store' });
    return { status: res.status, ok: res.ok, body: await res.text() };
  },
};

/**
 * Commands that already have a purpose-built, non-`invoke` browser path, so
 * routing them through the bridge would add a redundant hop.
 */
export const NATIVE_BROWSER_PATH: Record<string, string> = {
  compute_ghost_curve:
    'hooks/ghostLineComputation.ts computes the projection in pure TS, with the ' +
    'regression windows pinned to the Rust engines (predictive::OLS_MAX_WINDOW / ' +
    'vwepr::MAX_WINDOW) so both paths fit over the same bars.',
  get_historical_view:
    'charting/datafeed.ts falls through to paged Kite REST (fetchKiteBatch + ' +
    'scrollBackCache), which is the browser history path and already handles ' +
    "Kite's per-interval day caps.",
  load_historical:
    'Kite→QuestDB backfill is a server-side ingestion concern; the hosted ' +
    'deployment runs it continuously via history_loader.rs. No frontend caller.',
};

/**
 * Commands that are meaningless in a browser. Callers already guard these with
 * `isTauri()`; the table exists so the coverage test can tell "deliberately N/A"
 * apart from "forgotten".
 */
export const NOT_APPLICABLE_ON_WEB: Record<string, string> = {
  check_for_update: 'A page reload IS the update on the web.',
  install_update: 'A page reload IS the update on the web.',
  relaunch_app: 'A page reload IS the update on the web.',
};

/**
 * Commands whose HTTP equivalent is planned but not yet deployed. Listed
 * explicitly so `bridgeInvoke` can say WHICH surface is missing instead of
 * failing with a generic "unsupported".
 *
 * Empty: every command the frontend actually calls now has a web path. Keep the
 * table — it is the honest landing place for the next command added to
 * `invoke_handler![]` before its route exists, and the coverage test requires
 * every command to appear in exactly one of these tables.
 */
export const PENDING_SERVER_ROUTE: Record<string, string> = {};

/**
 * Commands registered in `invoke_handler![]` that no frontend code calls.
 *
 * Not gaps, and deliberately not given adapters: writing one would be building a
 * server route for a caller that does not exist. Listed rather than omitted so the
 * coverage test still accounts for them, and so a future caller finds this note
 * instead of assuming the command works on the web.
 */
export const NO_FRONTEND_CALLER: Record<string, string> = {
  get_trade_history:
    'No call site. This was the read side of the simulated paper-trading journal, ' +
    'which has been removed from the app entirely — there is no trade history to ' +
    'show and nothing writes one.',
  deploy_ai_sentinel:
    'No call site. The sentinel monitor loop is desktop-resident background work ' +
    'with no UI entry point; a hosted deployment would run it server-side.',
};
