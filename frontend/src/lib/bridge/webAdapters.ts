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
}

/** The tagged union `commands/instruments.rs::SearchResult` serializes to. */
export type SearchResult =
  | { kind: 'EQ'; symbol: string; name: string; exchange: string }
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
      });
    }
  }
  return out;
}

async function searchExchange(query: string, exchange: string): Promise<InstrumentRow[]> {
  try {
    const data = await apiJson<{ results?: InstrumentRow[] }>(
      `/api/kite/instruments?q=${encodeURIComponent(query)}&exchange=${exchange}`,
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

/** Start an agent run and stream it. Returns the thread id immediately. */
async function startAgentRun(args: Args): Promise<string> {
  const symbol = reqStr(args, 'symbol', 'run_deep_quant_agent');
  const mode = optStr(args, 'mode') ?? 'FIND';
  const profile = optStr(args, 'profile') ?? 'INTRADAY';
  const timeframe = optStr(args, 'timeframe');
  const manualTrade = (args.manual_trade ?? args.manualTrade) as Record<string, unknown> | undefined;

  // Same id format as the Rust command, so persisted Python threads look alike.
  const threadId = `thread_${symbol}_${Date.now()}`;

  await emitPreRunConsensus(symbol, timeframe ?? '10m');

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

const WORKSPACE_KEY = (symbol: string) => `stratai.workspace.${symbol}`;
const RADAR_KEY = 'stratai.radar.symbols';
const PORTFOLIO_KEY = 'stratai.paper.portfolio';

/** The opening balance from `lib.rs:204`, so both paths start from ₹10,00,000. */
const PAPER_OPENING_BALANCE = 1000000.0;

interface PaperPosition {
  id: string;
  symbol: string;
  side: string;
  entry_price: number;
  quantity: number;
  take_profit: number;
  stop_loss: number;
  status: string;
}

interface PaperPortfolio {
  balance: number;
  active_positions: PaperPosition[];
  trade_history: PaperPosition[];
}

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

/**
 * The stored paper portfolio, or a fresh one at the opening balance.
 *
 * Total: a corrupt or partial value reads as a fresh portfolio rather than
 * throwing, because the desktop command cannot fail this way and a caller that
 * crashed here would leave the trade panel stuck.
 */
function readPortfolio(): PaperPortfolio {
  const fresh: PaperPortfolio = {
    balance: PAPER_OPENING_BALANCE,
    active_positions: [],
    trade_history: [],
  };
  const raw = readLocal(PORTFOLIO_KEY);
  if (!raw) return fresh;
  try {
    const parsed = JSON.parse(raw) as Partial<PaperPortfolio>;
    return {
      balance: Number.isFinite(parsed.balance) ? (parsed.balance as number) : PAPER_OPENING_BALANCE,
      active_positions: Array.isArray(parsed.active_positions) ? parsed.active_positions : [],
      trade_history: Array.isArray(parsed.trade_history) ? parsed.trade_history : [],
    };
  } catch {
    return fresh;
  }
}

function writePortfolio(portfolio: PaperPortfolio): void {
  writeLocal(PORTFOLIO_KEY, JSON.stringify(portfolio));
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
    `SELECT DISTINCT expiry FROM option_chain_snapshots WHERE ${underlyingClause(underlying)}`,
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
    // equities first; two exchange calls in parallel reproduce that ordering.
    const [eq, fno] = await Promise.all([
      searchExchange(query, 'NSE'),
      searchExchange(query, 'NFO'),
    ]);
    return rowsToSearchResults([...eq, ...fno]);
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
    const threadId = reqStr(args, 'thread_id', 'cancel_deep_quant_agent');
    activeRuns.get(threadId)?.abort();
    activeRuns.delete(threadId);
    // Best-effort: ask Python to break out of astream at the next step boundary.
    try {
      await fetch('/api/deepquant/cancel', postJson({ thread_id: threadId }));
    } catch (err) {
      console.warn('[bridge] cancel signal to deep-quant failed:', err);
    }
    return undefined;
  },

  ask_trade_question: async (args) => {
    const threadId = reqStr(args, 'thread_id', 'ask_trade_question');
    const question = reqStr(args, 'question', 'ask_trade_question');
    const payload = {
      thread_id: threadId,
      question,
      model: optStr(args, 'model') ?? null,
      user_id: optStr(args, 'user_id') ?? optStr(args, 'userId') ?? null,
    };

    void (async () => {
      let sawRunFinished = false;
      try {
        const res = await fetch('/api/deepquant/qa', postJson(payload));
        if (!res.ok || !res.body) {
          throw await failure(res, `deep-quant /qa failed with HTTP ${res.status}`);
        }
        await relaySse(res.body, (frame) => {
          if (frame.event === 'RUN_FINISHED') sawRunFinished = true;
          emitBridgeEvent('deep-quant-qa-stream', { event: frame.event, data: frame.data });
        });
      } catch (err) {
        emitBridgeEvent('deep-quant-qa-stream', {
          event: 'ERROR',
          data: { error: err instanceof Error ? err.message : String(err) },
        });
        return;
      }
      // Synthetic completion so the UI always leaves its streaming state, as
      // `ask_trade_question` does when the stream ends without RUN_FINISHED.
      if (!sawRunFinished) {
        emitBridgeEvent('deep-quant-qa-stream', {
          event: 'RUN_FINISHED',
          data: { thread_id: threadId, status: 'completed' },
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
  fno_list_chains: async () => {
    const rows = await questdbRows(
      'SELECT DISTINCT underlying, expiry FROM option_chain_snapshots ORDER BY underlying, expiry',
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
      `SELECT DISTINCT expiry FROM option_chain_snapshots WHERE ${underlyingClause(underlying)} ORDER BY expiry ASC`,
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
      `SELECT count() FROM option_chain_snapshots WHERE ${underlyingClause(underlying)}`,
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

  // ── Paper trading ─────────────────────────────────────────────────────────
  // Desktop keeps the virtual portfolio in Tauri managed state
  // (`lib.rs:203` — `Mutex<VirtualPortfolio>`), so it is per-device AND resets on
  // every app launch. `localStorage` is the same per-device scope that survives a
  // reload, so this is a strict improvement rather than a degraded stand-in. A
  // server-side store keyed by the JWT `user_id` is the real upgrade (it would
  // follow the user across devices) but needs auth plumbing that does not exist.
  execute_paper_trade: async (args) => {
    const symbol = reqStr(args, 'symbol', 'execute_paper_trade');
    const side = reqStr(args, 'side', 'execute_paper_trade');
    const entryPrice = reqNum(args, 'entryPrice' in args ? 'entryPrice' : 'entry_price', 'execute_paper_trade');
    const stopLoss = reqNum(args, 'stopLoss' in args ? 'stopLoss' : 'stop_loss', 'execute_paper_trade');
    const takeProfit = reqNum(args, 'takeProfit' in args ? 'takeProfit' : 'take_profit', 'execute_paper_trade');

    const portfolio = readPortfolio();

    // Position sizing copied from `execution/paper.rs`: risk exactly 2% of balance
    // over the stop distance, with the same degenerate-distance and minimum-size
    // guards, so the two paths size a trade identically.
    const riskAmount = portfolio.balance * 0.02;
    const slDistance = Math.abs(entryPrice - stopLoss);
    const sized = slDistance > 1e-6 ? Math.round(riskAmount / slDistance) : 10;
    const quantity = Math.max(1, sized);

    portfolio.active_positions.push({
      id: `${symbol}-${Date.now()}`,
      symbol,
      side,
      entry_price: entryPrice,
      quantity,
      take_profit: takeProfit,
      stop_loss: stopLoss,
      status: 'OPEN',
    });
    writePortfolio(portfolio);

    // The desktop command emits this so `useTradeStore` refreshes without polling.
    emitBridgeEvent('paper_portfolio_update', portfolio);

    return `Trade executed successfully! Deployed ${quantity} units of ${symbol} (Risking 2% on stop-loss distance).`;
  },

  get_paper_portfolio: async () => readPortfolio(),

  log_completed_trade: async (args) => {
    const portfolio = readPortfolio();
    const symbol = optStr(args, 'symbol');
    // Move the matching open position into history; with no match, record what was
    // supplied so the journal is not silently short an entry.
    const idx = portfolio.active_positions.findIndex((p) => p.symbol === symbol);
    const closed =
      idx >= 0
        ? { ...portfolio.active_positions.splice(idx, 1)[0], status: optStr(args, 'status') ?? 'CLOSED' }
        : {
            id: `${symbol ?? 'unknown'}-${Date.now()}`,
            symbol: symbol ?? 'unknown',
            side: optStr(args, 'side') ?? '',
            entry_price: Number(args.entry_price ?? args.entryPrice) || 0,
            quantity: Number(args.quantity) || 0,
            take_profit: Number(args.take_profit ?? args.takeProfit) || 0,
            stop_loss: Number(args.stop_loss ?? args.stopLoss) || 0,
            status: optStr(args, 'status') ?? 'CLOSED',
          };
    portfolio.trade_history.push(closed);
    writePortfolio(portfolio);
    return undefined;
  },

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
    const res = await fetch(`/api/kite${rel}`, { cache: 'no-store' });
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
    'No call site. `useQuantStore` writes the journal via log_completed_trade but ' +
    'nothing reads it back; the trade history the UI shows comes from ' +
    'get_paper_portfolio.trade_history.',
  deploy_ai_sentinel:
    'No call site. The sentinel monitor loop is desktop-resident background work ' +
    'with no UI entry point; a hosted deployment would run it server-side.',
};
