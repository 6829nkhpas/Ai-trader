/**
 * charting/datafeed.ts — TradingView Advanced Charts JS API Datafeed adapter.
 *
 * Bridges the existing Zerodha/Kite data pipeline to the TradingView widget's
 * IBasicDatafeed interface. Reuses the project's existing data-fetching logic:
 *   - Historical candles: Kite Historical API via aggregator proxy `/kite/historical`
 *   - Live ticks: Zustand `useTradeStore.ohlcCandles`, fed by the /ws/* sockets
 *   - Symbol search: `/kite/quote` endpoint
 *
 * No new backend endpoints are needed — this is a pure frontend adapter.
 */

import type {
  IBasicDatafeed,
  OnReadyCallback,
  SearchSymbolsCallback,
  ResolveCallback,
  ErrorCallback,
  HistoryCallback,
  SubscribeBarsCallback,
  Bar,
  LibrarySymbolInfo,
  ResolutionString,
  PeriodParams,
  DatafeedConfiguration,
} from './datafeedTypes';
import { useTradeStore, type OhlcCandle } from '../store/useTradeStore';
import { kiteFetch } from '../lib/kiteFetch';
import { bridgeInvoke } from '../lib/bridge';
import { debugLog } from '../lib/debugLog';

// ── Resolution Mapping ────────────────────────────────────────────────────
// Maps TV resolution strings to Kite Historical API interval strings.

const RESOLUTION_TO_KITE_INTERVAL: Record<string, string> = {
  '1':    'minute',
  '2':    'minute',
  '3':    '3minute',
  '4':    'minute',
  '5':    '5minute',
  '10':   '10minute',
  '15':   '15minute',
  '30':   '30minute',
  '60':   '60minute',
  '75':   '15minute',
  '120':  '60minute',
  '125':  '15minute',
  '180':  '60minute',
  '240':  '60minute',
  '1D':   'day',
  'D':    'day',
  '1W':   'day',
  'W':    'day',
  '1M':   'day',
  'M':    'day',
};

/**
 * Kite's per-interval day cap for a single `/instruments/historical` request.
 * Asking for a window wider than this returns `[]` with no error, which is
 * exactly what stops scroll-back dead. Source: Kite Historical API docs.
 */
const KITE_INTERVAL_MAX_DAYS: Record<string, number> = {
  minute: 7,
  '3minute': 30,
  '5minute': 30,
  '10minute': 30,
  '15minute': 60,
  '30minute': 60,
  '60minute': 60,
  day: 2000,
};

/** Convert TV resolution to a UI timeframe string for the Tauri IPC. */
export const RESOLUTION_TO_TIMEFRAME: Record<string, string> = {
  '1':   '1m',  '2':   '2m',  '3':   '3m',  '4':   '4m',
  '5':   '5m',  '10':  '10m', '15':  '15m', '30':  '30m',
  '60':  '1h',  '75':  '75m', '120': '2h',  '125': '125m',
  '180': '3h',  '240': '4h',
  '1D':  '1D',  'D':   '1D',
  '1W':  '1W',  'W':   '1W',
  '1M':  '1M',  'M':   '1M',
};

/**
 * How many Kite history pages to request at once.
 *
 * Kite rate-limits historical requests to 3/sec, so this stays at 3 — enough to
 * collapse a serial chain of round trips into a third of the wall time without
 * tripping the limit.
 */
const KITE_PAGE_CONCURRENCY = 3;

/** All supported resolutions for the symbol info. */
const SUPPORTED_RESOLUTIONS: ResolutionString[] = [
  '1', '2', '3', '4', '5', '10', '15', '30',
  '60', '75', '120', '125', '180', '240',
  '1D', '1W', '1M',
];


// ── In-memory scroll-back cache (per symbol + timeframe) ───────────────────
//
// TradingView calls `getBars` once per scroll-back page with the [from, to]
// window it wants. Without persistence, each call returns ONLY the bars fetched
// that turn — so when TV asks for a window that overlaps a slice we already
// pulled, we hit the network again and TV shows whatever Kite happened to
// return this time (often nothing on a cold page). By merging every fetched
// bar into this map keyed by `SYMBOL::TIMEFRAME`, we can serve any overlapping
// window from memory instantly, and only fetch the missing older slice.
const scrollBackCache = new Map<string, Map<number, Bar>>();

function scrollBackKey(symbol: string, timeframe: string): string {
  return `${symbol.toUpperCase()}::${timeframe}`;
}

function readScrollBackCache(symbol: string, timeframe: string, fromMs: number, toMs: number): Bar[] {
  const store = scrollBackCache.get(scrollBackKey(symbol, timeframe));
  if (!store) return [];
  const out: Bar[] = [];
  for (const bar of store.values()) {
    if (bar.time >= fromMs && bar.time <= toMs) out.push(bar);
  }
  out.sort((a, b) => a.time - b.time);
  return out;
}

function mergeScrollBackCache(symbol: string, timeframe: string, bars: Bar[]): void {
  if (bars.length === 0) return;
  const key = scrollBackKey(symbol, timeframe);
  let store = scrollBackCache.get(key);
  if (!store) {
    store = new Map<number, Bar>();
    scrollBackCache.set(key, store);
  }
  for (const b of bars) store.set(b.time, b);
}

/** Drop every scroll-back cache entry for a symbol (called on symbol change). */
export function invalidateScrollBackCache(symbol: string): void {
  const prefix = `${symbol.toUpperCase()}::`;
  for (const key of scrollBackCache.keys()) {
    if (key.startsWith(prefix)) scrollBackCache.delete(key);
  }
  for (const key of exhaustedWindows.keys()) {
    if (key.startsWith(prefix)) exhaustedWindows.delete(key);
  }
}

// ── Exhaustion tracking ────────────────────────────────────────────────────
// Tracks `SYMBOL::TIMEFRAME::fromSec` windows where Kite genuinely returned
// zero candles (confirmed empty, NOT a transient network failure). Only when
// a window is in this set do we tell TradingView `noData: true` — which makes
// TV stop paginating further. Without this, a single network glitch would
// permanently kill scroll-back for the session.
const exhaustedWindows = new Set<string>();

function exhaustionKey(symbol: string, timeframe: string, fromSec: number): string {
  return `${symbol.toUpperCase()}::${timeframe}::${fromSec}`;
}

/** Get the earliest bar time (ms) we've ever seen for a symbol+timeframe. */
function earliestKnownBar(symbol: string, timeframe: string): number | undefined {
  const store = scrollBackCache.get(scrollBackKey(symbol, timeframe));
  if (!store || store.size === 0) return undefined;
  let min = Infinity;
  for (const t of store.keys()) {
    if (t < min) min = t;
  }
  return min === Infinity ? undefined : min;
}

// ── Instrument Token Cache ────────────────────────────────────────────────
const tokenCache = new Map<string, number>();

/**
 * The Kite instrument token for `symbol`, or null when it cannot be identified.
 *
 * Matches on the EXACT tradingsymbol only. Both lookups used to fall back to the
 * first row of whatever came back (`quotes[0]`, `results[0]`), and that is how
 * SENSEX got a chart of something else entirely: SENSEX is a BSE index, so a
 * search against NSE returns the ETFs that track it — SENSEXADD, SENSEXETF,
 * SENSEXBEES — and the first of those was charted under the SENSEX name at around
 * ₹80 while the index itself sits near 77,000. A near-miss is not a match, and a
 * chart labelled with one instrument showing another is worse than an empty one.
 */
async function resolveInstrumentToken(symbol: string, exchange: string = 'NSE'): Promise<number | null> {
  const cacheKey = `${exchange}:${symbol}`.toUpperCase();
  const cached = tokenCache.get(cacheKey);
  if (cached) return cached;
  const wanted = symbol.trim().toUpperCase();
  try {
    const res = await kiteFetch(`/quote?i=${exchange}:${encodeURIComponent(symbol)}`);
    if (res.ok) {
      const data = await res.json();
      const quotes = data.quotes as { symbol: string; instrument_token: number }[] | undefined;
      const token = quotes?.find((q) => q.symbol?.toUpperCase() === wanted)?.instrument_token;
      if (token) {
        tokenCache.set(cacheKey, token);
        return token;
      }
    }

    const resInst = await kiteFetch(`/instruments?q=${encodeURIComponent(symbol)}&exchange=${encodeURIComponent(exchange)}`);
    if (resInst.ok) {
      const data = await resInst.json();
      const results = data.results as { tradingsymbol: string; instrument_token: number }[] | undefined;
      const token = results?.find((r) => r.tradingsymbol?.toUpperCase() === wanted)?.instrument_token;
      if (token) {
        tokenCache.set(cacheKey, token);
        return token;
      }
    }
    return null;
  } catch {
    return null;
  }
}

// ── FNO symbol detection ──────────────────────────────────────────────────
function isFnoSymbol(symbol: string): boolean {
  const s = symbol?.trim()?.toUpperCase();
  if (!s) return false;
  if (s.endsWith('FUT')) return true;
  if ((s.endsWith('CE') || s.endsWith('PE')) && /\d/.test(s)) return true;
  return false;
}

// ── Kite Batch Fetch ──────────────────────────────────────────────────────

interface KiteCandleRaw {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/**
 * Exported for `__tests__/datafeed.fnoPaging.test.ts`, which pins the page
 * ordering: an option contract's oldest page is legitimately empty and must not
 * abort the fetch before the recent pages that hold its candles.
 */
export async function fetchKiteBatch(
  symbol: string,
  interval: string,
  from: Date,
  to: Date,
  exchange: string = 'NSE',
  timeframe?: string,
): Promise<Bar[]> {
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  const parseCandles = (data: { candles?: KiteCandleRaw[] }): Bar[] =>
    (data.candles || [])
      .map((c) => ({
        time: c.time * 1000, // TV expects UTC milliseconds
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
      }))
      .filter((c) => c.time > 0 && c.open > 0);

  const maxDays = KITE_INTERVAL_MAX_DAYS[interval] ?? 60;
  const dayMs = 24 * 60 * 60 * 1000;
  const pages: { from: Date; to: Date }[] = [];
  let pageEnd = new Date(to);
  while (pageEnd.getTime() >= from.getTime()) {
    const pageStart = new Date(Math.max(from.getTime(), pageEnd.getTime() - (maxDays - 1) * dayMs));
    pages.push({ from: pageStart, to: pageEnd });
    if (pageStart.getTime() <= from.getTime()) break;
    pageEnd = new Date(pageStart.getTime() - dayMs);
  }

  // `pages` is built walking BACKWARDS from `to`, so it is already newest-first,
  // and it deliberately stays that way. It used to be reversed to oldest-first,
  // which combined with the "stop once a page comes back empty" exit below to
  // discard every F&O chart:
  //
  //   TradingView's opening window spans months, but an option contract is listed
  //   weeks before expiry. The oldest slice of that window therefore predates the
  //   contract's existence and Kite correctly answers `[]` — whereupon the loop
  //   broke on its FIRST batch and returned nothing, having never requested the
  //   recent pages that hold the actual candles. Measured against production:
  //   RELIANCE26SEP1080CE returns 100 ten-minute candles for Aug 25-30 and 0 for
  //   Feb 1 - Mar 2, and the chart rendered "No data here".
  //
  // Equities never tripped it because a cash symbol has bars in every page.
  //
  // Newest-first makes the early exit mean what it was meant to mean: stop
  // extending FURTHER BACK once history runs out, rather than give up before
  // reaching the present. Order does not matter downstream — `mergeScrollBackCache`
  // is keyed by bar time and `getBars` sorts before handing bars to TV.

  // ── Kite Historical REST pages ──────────────────────────────────────────
  //
  // F&O symbols used to short-circuit here and return only bars fetched over
  // Tauri IPC, on the stated grounds that "the REST /kite/historical proxy cannot
  // resolve FNO instrument tokens and always returns 0 candles". That was false by
  // the time the desktop shell was retired — `kite_api.rs::resolve_token` detects
  // an F&O tradingsymbol (digits, ending CE/PE/FUT) and looks it up against the
  // NFO exchange. Measured against the running proxy: BANKNIFTY26AUGFUT returned
  // 104 15-minute candles and NIFTY26AUG24250CE returned 52, while an illiquid
  // strike returned 0 — which is a real absence of trades, not a resolution
  // failure. So F&O now pages REST exactly like equities, and the early return
  // that would otherwise leave every F&O chart empty is gone.
  const all: Bar[] = [];

  /** Fetch one page, falling back to a token lookup when the symbol form is empty. */
  const fetchPage = async (page: { from: Date; to: Date }): Promise<Bar[]> => {
    const dateParams = `&from=${fmt(page.from)}&to=${fmt(page.to)}`;
    try {
      const url = `/historical?symbol=${encodeURIComponent(symbol)}&interval=${interval}${dateParams}`;
      const response = await kiteFetch(url);
      if (response.ok) {
        const candles = parseCandles(await response.json());
        if (candles.length > 0) return candles;
      }

      const token = await resolveInstrumentToken(symbol, exchange);
      if (!token) return [];
      const tokenUrl = `/historical?instrument_token=${token}&interval=${interval}${dateParams}`;
      const tokenResponse = await kiteFetch(tokenUrl);
      if (!tokenResponse.ok) return [];
      return parseCandles(await tokenResponse.json());
    } catch (err) {
      console.warn('[Datafeed] Kite page fetch failed:', err);
      return [];
    }
  };

  // Pages used to be walked STRICTLY one at a time, each awaiting the previous
  // one's full round trip (plus a possible second token-resolution request). A
  // 1m chart pages in 7-day slices, so a wide initial TradingView window meant a
  // long serial chain of gateway round trips before the first bar rendered.
  //
  // They are independent requests, so they run in small concurrent batches now.
  // Batching rather than one big Promise.all does two things: it stays polite to
  // Kite's 3 req/s ceiling, and it preserves the original "stop once a page
  // comes back empty" early exit (at batch granularity) so we don't fan out
  // requests for history that does not exist.
  //
  // Because `pages` runs newest → oldest, an empty batch means we have walked off
  // the front of the instrument's history, so everything still unrequested is
  // older and also empty. Stopping there is safe; stopping on an empty OLDEST
  // batch was not.
  for (let i = 0; i < pages.length; i += KITE_PAGE_CONCURRENCY) {
    const batch = pages.slice(i, i + KITE_PAGE_CONCURRENCY);
    const settled = await Promise.all(batch.map(fetchPage));
    const got = settled.flat();
    if (got.length === 0) break;
    all.push(...got);
  }

  return all;
}

/**
 * Ask TradingView to re-request bars for every live subscription on `symbol`.
 *
 * TV hands each subscription an `onResetCacheNeededCallback`; calling it makes
 * the widget drop its own bar cache and call `getBars` again.
 */
function requestChartReset(symbol: string): void {
  const upper = symbol.toUpperCase();
  for (const sub of activeSubscriptions.values()) {
    if (sub.symbol.toUpperCase() !== upper) continue;
    try {
      sub.onResetCacheNeeded?.();
    } catch (err) {
      console.warn('[Datafeed] chart reset callback failed:', err);
    }
  }
}

// ── Live Subscription Manager ─────────────────────────────────────────────

interface LiveSubscription {
  symbol: string;
  resolution: string;
  onTick: SubscribeBarsCallback;
  /** TV's "drop your bar cache and re-request" hook — see `requestChartReset`. */
  onResetCacheNeeded?: () => void;
  unsubscribe: () => void;
}

const activeSubscriptions = new Map<string, LiveSubscription>();

function startLiveSubscription(
  symbol: string,
  resolution: string,
  onTick: SubscribeBarsCallback,
  listenerGuid: string,
  onResetCacheNeeded?: () => void,
): void {
  const symbolUpper = symbol.toUpperCase();
  let lastBarTime = 0;
  let tickCount = 0;
  let droppedOutOfOrder = 0;
  /** Fingerprint of the last bar handed to TradingView — see the dedupe below. */
  let lastForwarded = '';

  const forwardCandle = (candle: { symbol: string; start_timestamp_ms: number; open: number; high: number; low: number; close: number; volume?: number }) => {
    if (candle.symbol.toUpperCase() !== symbolUpper) return;
    const barTimeMs = candle.start_timestamp_ms;

    // Drop bars that are byte-identical to the one we last forwarded.
    //
    // This subscription fires on EVERY store write, and the live feed rebroadcasts
    // the in-progress candle for every tick of every subscribed instrument (755 of
    // them in production). The store coalesces those into one write per animation
    // frame, so this ran ~60×/s and called `onTick` every time — even when THIS
    // symbol's bar had not changed at all, because some unrelated symbol ticked.
    //
    // `onTick` is an IPC call into the TradingView iframe. Sixty redundant ones a
    // second saturate that channel, and everything else that has to cross it gets
    // slower — including `createMultipointShape`, which the ghost line needs ~20
    // awaited round-trips of per redraw. That is how a working ghost line stopped
    // completing a draw once live ticks started flowing: not a fault in the ghost
    // line, but starvation of the bridge it draws over.
    //
    // Forwarding an identical bar tells TradingView nothing it does not already
    // have, so this is a pure saving.
    const fingerprint = `${barTimeMs}|${candle.open}|${candle.high}|${candle.low}|${candle.close}|${candle.volume ?? 0}`;
    if (fingerprint === lastForwarded) return;
    lastForwarded = fingerprint;

    // TradingView requires bars in non-decreasing time order and drops (and
    // logs a "time order violation" for) anything older than the last bar it
    // received. An EQUAL timestamp is an in-place update of the forming bar and
    // must still be forwarded, hence `>=`.
    if (barTimeMs < lastBarTime) {
      droppedOutOfOrder++;
      if (droppedOutOfOrder <= 3) {
        console.warn(
          `[Datafeed] Dropping out-of-order bar for ${symbolUpper}: ` +
          `${new Date(barTimeMs).toISOString()} arrived after ${new Date(lastBarTime).toISOString()}`,
        );
      }
      return;
    }

    lastBarTime = barTimeMs;
    tickCount++;
    if (tickCount <= 5) {
      debugLog(`[Datafeed] Live tick #${tickCount} for ${symbolUpper}: time=${barTimeMs} O=${candle.open} H=${candle.high} L=${candle.low} C=${candle.close}`);
    }
    onTick({
      time: barTimeMs,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      volume: candle.volume ?? 0,
    });
  };

  // ── Live tick path: Zustand store subscription ────────────────────────
  // The WS feeds land in `useTradeStore.ohlcCandles` (see the socket bootstrap in
  // `app/page.tsx`), so watching the store is how live bars reach the chart.
  //
  // This callback runs on EVERY store write — including the large
  // `setHistoricalCache` writes the datafeed itself makes — and each run scans the
  // whole `ohlcCandles` array (capped at 3 000), so loading history triggers
  // full-array scans in the tick path. Worth revisiting if tick latency regresses;
  // a symbol-keyed selector would avoid the scan.
  const unsub = useTradeStore.subscribe((state) => {
    // Pick the NEWEST bar for this symbol, not the array-last one.
    //
    // `ohlcCandles` is appended in ARRIVAL order, so a bar that reaches the
    // feed late (a 06:50 bar arriving after 06:55) lands at the end of the
    // array. Reading the last element therefore handed TradingView an older
    // timestamp than the one it already had: the bar was rejected as a time
    // order violation, AND because the real newest bar was no longer array-last
    // its subsequent updates stopped being forwarded too — the chart froze with
    // a gap until a brand-new bar happened to arrive. Scanning for the maximum
    // timestamp makes the late bar a no-op instead of a stall.
    let newest: (typeof state.ohlcCandles)[number] | null = null;
    for (const c of state.ohlcCandles) {
      if (c.symbol.toUpperCase() !== symbolUpper) continue;
      if (newest === null || c.start_timestamp_ms >= newest.start_timestamp_ms) newest = c;
    }
    if (newest === null) return;
    forwardCandle(newest);
  });

  debugLog(
    `[Datafeed] subscribeBars: ${symbolUpper} (resolution=${resolution}, guid=${listenerGuid.slice(0, 8)}…)`,
  );

  activeSubscriptions.set(listenerGuid, {
    symbol, resolution, onTick, onResetCacheNeeded,
    unsubscribe: unsub,
  });
}

// ── REST fallback for symbol search (used when the `search_instruments` adapter
//    fails). Mirrors the old REST proxy behaviour:
//    GET /kite/instruments?q=<query>&exchange=<ex> → equity + index rows only.
async function fallbackRestSearch(
  userInput: string,
  exchange: string,
  onResult: SearchSymbolsCallback,
): Promise<void> {
  if (!userInput || userInput.length < 1) {
    onResult([]);
    return;
  }

  const ex = exchange || 'NSE';
  try {
    const res = await kiteFetch(`/instruments?q=${encodeURIComponent(userInput)}&exchange=${encodeURIComponent(ex)}`);
    if (!res.ok) {
      onResult([]);
      return;
    }
    const data = await res.json();
    const results = (data.results || []) as {
      tradingsymbol: string;
      name: string;
      exchange: string;
      instrument_type: string;
      segment?: string;
    }[];
    onResult(
      results.map((inst) => ({
        symbol: inst.tradingsymbol,
        full_name: `${inst.exchange}:${inst.tradingsymbol}`,
        description: inst.name,
        exchange: inst.exchange,
        ticker: `${inst.exchange}:${inst.tradingsymbol}`,
        // `segment`, not `instrument_type`. Kite reports `instrument_type: "EQ"`
        // for index rows too — `NIFTY 50` comes back as segment `INDICES`, type
        // `EQ` — so the old `instrument_type === 'INDEX'` test matched nothing
        // and every index was labelled a stock.
        type: (inst.segment ?? '').toUpperCase() === 'INDICES' ? 'index' : 'stock',
      })),
    );
  } catch {
    onResult([]);
  }
}

// ── Datafeed Implementation ───────────────────────────────────────────────

export function createDatafeed(): IBasicDatafeed {
  return {
    onReady(callback: OnReadyCallback): void {
      // TV requires async callback
      setTimeout(() => {
        const config: DatafeedConfiguration = {
          // Single "ALL" exchange entry so TradingView's exchange filter does
          // NOT pre-filter the results — the user picks any symbol across
          // NSE / BSE / NFO from one flat, global result list.
          exchanges: [
            { value: 'ALL', name: 'All', desc: 'All Exchanges (NSE / BSE / NFO)' },
          ],
          // Single "All" symbol type so the type filter doesn't narrow by
          // stock / index / fno either — one global search across everything.
          symbols_types: [
            { name: 'All', value: 'all' },
          ],
          supported_resolutions: SUPPORTED_RESOLUTIONS,
          supports_marks: false,
          supports_timescale_marks: false,
          supports_time: true,
        };
        callback(config);
      }, 0);
    },

    searchSymbols(
      userInput: string,
      _exchange: string,
      _symbolType: string,
      onResult: SearchSymbolsCallback,
    ): void {
      if (!userInput || userInput.length < 1) {
        onResult([]);
        return;
      }

      const query = userInput.trim();

      // ── Global search across NSE / BSE / NFO via the existing
      // `search_instruments` command. It returns EQ + Index + FNO (CE/PE/FUT)
      // rows — from the SQLite `instruments` + `nfo_instruments` tables on
      // desktop, from the Kite instrument proxy in a browser — so one call
      // returns the full global result set. We map every row into TV's
      // `SearchSymbolResultItem` shape without filtering by exchange or type,
      // so the user sees equities, indexes, and F&O contracts in one flat list.
      bridgeInvoke<
        Array<
          | { kind: 'EQ'; symbol: string; name: string; exchange: string; segment?: string }
          | {
              kind: 'FNO';
              tradingsymbol: string;
              underlying: string;
              expiry: string;
              strike: number | null;
              optionType: string;
            }
        >
      >('search_instruments', { query })
          .then((results) => {
            const items = (results || []).map((r) => {
              if (r.kind === 'EQ') {
                const upper = r.symbol.toUpperCase();
                // Kite's `INDICES` segment is the authoritative answer, and it
                // covers all 209 index rows the NSE and BSE masters publish. The
                // seven names hardcoded here before meant every other index —
                // NIFTY IT, NIFTY MIDCAP 100, BANKEX, the lot — was labelled a
                // stock in TradingView's own search list.
                const isIndex = r.segment
                  ? r.segment.toUpperCase() === 'INDICES'
                  : upper === 'NIFTY' ||
                    upper === 'NIFTY 50' ||
                    upper === 'BANKNIFTY' ||
                    upper === 'NIFTY BANK' ||
                    upper === 'FINNIFTY' ||
                    upper === 'MIDCPNIFTY' ||
                    upper === 'SENSEX';
                return {
                  symbol: r.symbol,
                  full_name: `${r.exchange}:${r.symbol}`,
                  description: r.name,
                  exchange: r.exchange,
                  ticker: `${r.exchange}:${r.symbol}`,
                  type: isIndex ? 'index' : 'stock',
                };
              }
              const desc =
                r.optionType === 'FUT'
                  ? `${r.underlying} FUT (${r.expiry})`
                  : `${r.underlying} ${r.strike ?? ''} ${r.optionType} (${r.expiry})`;
              return {
                symbol: r.tradingsymbol,
                full_name: `NFO:${r.tradingsymbol}`,
                description: desc,
                exchange: 'NFO',
                ticker: `NFO:${r.tradingsymbol}`,
                type: 'fno',
              };
            });
            // An empty result is not necessarily "no such symbol": on the web
            // the NFO leg of the adapter depends on the Kite instrument proxy,
            // so fall through to the REST search rather than showing nothing.
            if (items.length === 0) {
              void fallbackRestSearch(userInput, '', onResult);
              return;
            }
            onResult(items);
          })
          .catch((err) => {
            console.warn('[Datafeed] search_instruments failed:', err);
            void fallbackRestSearch(userInput, '', onResult);
          });
    },

    resolveSymbol(
      symbolName: string,
      onResolve: ResolveCallback,
      onError: ErrorCallback,
    ): void {
      // Strip exchange prefix if present (e.g. "NSE:RELIANCE" → "RELIANCE")
      const cleanSymbol = symbolName.includes(':')
        ? symbolName.split(':')[1]
        : symbolName;
      const exchange = symbolName.includes(':')
        ? symbolName.split(':')[0]
        : 'NSE';

      setTimeout(() => {
        const symbolInfo: LibrarySymbolInfo = {
          name: cleanSymbol,
          full_name: `${exchange}:${cleanSymbol}`,
          ticker: `${exchange}:${cleanSymbol}`,
          description: cleanSymbol,
          type: exchange === 'INDICES' ? 'index' : 'stock',
          session: '0915-1530',
          timezone: 'Asia/Kolkata',
          exchange: exchange,
          listed_exchange: exchange,
          format: 'price',
          minmov: 1,
          pricescale: 100, // 2 decimal places (₹XX.XX)
          has_intraday: true,
          has_daily: true,
          has_weekly_and_monthly: true,
          supported_resolutions: SUPPORTED_RESOLUTIONS,
          intraday_multipliers: ['1', '2', '3', '4', '5', '10', '15', '30', '60', '75', '120', '125', '180', '240'],
          daily_multipliers: ['1'],
          weekly_multipliers: ['1'],
          monthly_multipliers: ['1'],
          volume_precision: 0,
          data_status: 'streaming',
          currency_code: 'INR',
        };

        // Verify symbol exists via quote API
        kiteFetch(`/quote?i=${exchange}:${encodeURIComponent(cleanSymbol)}`)
          .then((res) => {
            if (!res.ok) {
              // Still resolve — the symbol might work with historical API
              onResolve(symbolInfo);
              return;
            }
            return res.json().then(() => {
              onResolve(symbolInfo);
            });
          })
          .catch(() => {
            // Resolve anyway to avoid blocking the widget
            onResolve(symbolInfo);
          });
      }, 0);
    },

    async getBars(
      symbolInfo: LibrarySymbolInfo,
      resolution: ResolutionString,
      periodParams: PeriodParams,
      onResult: HistoryCallback,
      onError: ErrorCallback,
    ): Promise<void> {
      const symbol = symbolInfo.name;
      const exchange = symbolInfo.exchange || 'NSE';
      const kiteInterval = RESOLUTION_TO_KITE_INTERVAL[resolution] ?? 'minute';
      const timeframe = RESOLUTION_TO_TIMEFRAME[resolution] ?? '1m';

      // The exact [from, to] window TradingView is asking for. TV walks this
      // window backward one page at a time as the user scrolls the chart left.
      const from = new Date(periodParams.from * 1000);
      const to = new Date(periodParams.to * 1000);
      const fromMs = from.getTime();
      const toMs = to.getTime();

      try {
        // 1. Pull any bars we already fetched for this symbol/timeframe that
        //    overlap the requested window. If the cache already fully covers
        //    the window, we don't hit the network at all — TV re-renders from
        //    memory and scroll-back is instant.
        const cached = readScrollBackCache(symbol, timeframe, fromMs, toMs);

        // 2. Find the oldest cached bar inside the requested window. If we have
        //    a continuous run covering [oldestCachedAt, to], we only need to
        //    fetch the missing slice [from, oldestCachedAt). Otherwise fetch
        //    the full window.
        let fetchFrom = from;
        if (cached.length > 0) {
          const oldestCached = cached[0].time;
          if (oldestCached <= fromMs) {
            // Cache fully covers the window — no network roundtrip.
            onResult(cached, { noData: false });
            return;
          }
          fetchFrom = new Date(oldestCached - 1);
          // Re-filter cached bars to the actual gap we're filling.
        }

        // 3. Fetch the missing slice (or the whole window on a cold cache).
        let fetched = await fetchKiteBatch(
          symbol,
          kiteInterval,
          fetchFrom,
          new Date(toMs),
          exchange,
          timeframe,
        );

        // 4. Merge the freshly fetched bars into the persistent cache so the
        //    next scroll-back call can serve them from memory.
        if (fetched.length > 0) {
          mergeScrollBackCache(symbol, timeframe, fetched);
        }

        // 5. Read the FULL [from, to] window back out of the merged cache so
        //    TV gets a continuous bar set spanning exactly what it asked for,
        //    regardless of where the freshly-fetched slice landed.
        let bars = readScrollBackCache(symbol, timeframe, fromMs, toMs);

        // ── FNO-specific: detect if the symbol is an F&O contract ──────
        const symbolIsFno = isFnoSymbol(symbol);

        if (bars.length === 0) {
          const earliest = earliestKnownBar(symbol, timeframe);

          // For FNO symbols, the Tauri/QuestDB cache is the SOLE data source.
          // If the requested window is entirely before the earliest bar, there
          // is genuinely no older data — stop immediately with a nextTime hint.
          if (symbolIsFno && earliest !== undefined && toMs < earliest) {
            onResult([], { noData: true, nextTime: Math.floor(earliest / 1000) });
            return;
          }

          // For all symbols: double-check pattern. First empty hit returns
          // noData: false so TV retries. Second empty hit for the same window
          // returns noData: true to stop pagination.
          const exKey = exhaustionKey(symbol, timeframe, periodParams.from);
          if (exhaustedWindows.has(exKey)) {
            const meta: { noData: boolean; nextTime?: number } = { noData: true };
            if (earliest !== undefined) {
              meta.nextTime = Math.floor(earliest / 1000);
            }
            onResult([], meta);
          } else {
            exhaustedWindows.add(exKey);
            onResult([], { noData: false });
          }
          return;
        }

        // For FNO symbols only: if the fetch returned zero NEW bars and we're
        // past the cache edge, mark exhaustion so TV stops on the next page.
        // Don't do this for equity — REST scroll-back would be blocked.
        if (symbolIsFno && fetched.length === 0 && cached.length > 0) {
          const earliest = earliestKnownBar(symbol, timeframe);
          if (earliest !== undefined && fromMs < earliest) {
            const exKey = exhaustionKey(symbol, timeframe, periodParams.from);
            exhaustedWindows.add(exKey);
          }
        }

        // Sort ascending by time (TV requirement) — readScrollBackCache already
        // sorts, but be defensive in case of a future change.
        bars.sort((a, b) => a.time - b.time);

        // Deduplicate by time (safety net; the cache already dedups by key).
        const seen = new Set<number>();
        bars = bars.filter((b) => {
          if (seen.has(b.time)) return false;
          seen.add(b.time);
          return true;
        });

        // ── Mirror bars into the Zustand historicalCache ──────────────
        // The Deep Quant / Consensus pipeline gates on
        // `useTradeStore.historicalCache` (via symbolCandleCount) to know a
        // symbol has data. Each scroll-back page is MERGED with the existing
        // cache so pages accumulate rather than overwrite.
        try {
          const cacheKey = `${symbol.toUpperCase()}::${timeframe}::${kiteInterval}`;
          const store = useTradeStore.getState();
          const existing = store.historicalCache[cacheKey] ?? [];
          const mergedByTime = new Map<number, OhlcCandle>();
          for (const c of existing) mergedByTime.set(c.start_timestamp_ms, c);
          for (const b of bars) {
            mergedByTime.set(b.time, {
              symbol: symbol.toUpperCase(),
              start_timestamp_ms: b.time, // datafeed bars are in ms
              open: b.open,
              high: b.high,
              low: b.low,
              close: b.close,
              volume: b.volume ?? 0,
            });
          }
          const merged = Array.from(mergedByTime.values()).sort(
            (a, b) => a.start_timestamp_ms - b.start_timestamp_ms,
          );
          store.setHistoricalCache(cacheKey, merged);
        } catch (cacheErr) {
          console.warn('[Datafeed] historicalCache mirror failed:', cacheErr);
        }

        onResult(bars, { noData: false });
      } catch (err) {
        console.error('[Datafeed] getBars failed:', err);
        onError(String(err));
      }
    },

    subscribeBars(
      symbolInfo: LibrarySymbolInfo,
      resolution: ResolutionString,
      onTick: SubscribeBarsCallback,
      listenerGuid: string,
      onResetCacheNeededCallback: () => void,
    ): void {
      // The reset callback is retained (it used to be ignored) so a background
      // Kite backfill can trigger a repaint — see `requestChartReset`.
      startLiveSubscription(
        symbolInfo.name,
        resolution,
        onTick,
        listenerGuid,
        onResetCacheNeededCallback,
      );
    },

    unsubscribeBars(listenerGuid: string): void {
      const sub = activeSubscriptions.get(listenerGuid);
      if (sub) {
        sub.unsubscribe();
        activeSubscriptions.delete(listenerGuid);
      }
    },
  };
}
