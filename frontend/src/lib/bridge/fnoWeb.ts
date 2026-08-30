// lib/bridge/fnoWeb.ts — the browser's F&O chain resolution, over QuestDB.
//
// The desktop path answers the five `fno_*` commands by joining two stores: the
// SQLite `nfo_instruments` master (which strikes are LISTED) and QuestDB
// `option_chain_snapshots` (which strikes have OI right now). A browser has no
// SQLite, but it does not need one: `option_chain_snapshots` already carries the
// real tradingsymbol in its `symbol` column —
//
//   underlying="NIFTY 50" expiry="2026-07-14" strike=24150 option_type="CE"
//   symbol="NIFTY2671424150CE"
//
// — so nothing has to be synthesized from strike + expiry, and the two-store join
// collapses to one query. Verified against the live table, not assumed.
//
// The one honest difference from desktop: this sees only strikes the ingester has
// snapshotted, which is the bounded band around ATM rather than the full listed
// ladder. Every caller here wants a *tradable* contract, so a strike with no
// snapshot is not a useful answer anyway — but it does mean a far-OTM strike the
// user types by hand can resolve on desktop and not on the web.

/**
 * QuestDB stores index chains under whichever name the ingester was configured
 * with at the time, and the two conventions disagree: the NFO derivative side
 * uses `NIFTY`/`BANKNIFTY`, the spot side `NIFTY 50`/`NIFTY BANK`. Old rows exist
 * under both (the live table currently holds `NIFTY 50` *and* `NIFTY`), so every
 * query has to match either. Mirrors `fno_service.rs::underlying_alt_name`.
 */
const NAME_PAIRS: readonly (readonly [string, string])[] = [
  ['NIFTY', 'NIFTY 50'],
  ['BANKNIFTY', 'NIFTY BANK'],
  ['FINNIFTY', 'NIFTY FIN SERVICE'],
  ['MIDCPNIFTY', 'NIFTY MIDCAP SELECT'],
];

/**
 * The NSE spot-side name for an underlying: `NIFTY` → `NIFTY 50`. Stock
 * underlyings have one name and come back unchanged.
 *
 * Needed because `getUnderlyingFromSymbol` deliberately yields the NFO-side name
 * (`NIFTY`), which is right for a chain query and wrong for an equity chart —
 * `NSE:NIFTY` is not the index's Kite tradingsymbol. `useTradeStore` uses this
 * when a mode switch has to turn a contract into something an equity mode can
 * actually plot. Direction-safe: passing either name yields the spot one.
 */
export function spotUnderlyingName(underlying: string): string {
  const trimmed = underlying.trim();
  const upper = trimmed.toUpperCase();
  for (const [short, long] of NAME_PAIRS) {
    if (upper === short || upper === long) return long;
  }
  return trimmed;
}

/** Both names an underlying's rows could be stored under, deduped. */
export function underlyingCandidates(underlying: string): string[] {
  const u = underlying.trim();
  const upper = u.toUpperCase();
  for (const [short, long] of NAME_PAIRS) {
    if (upper === short) return [short, long];
    if (upper === long) return [long, short];
  }
  return [u];
}

/**
 * `live_ticks.symbol` values that could carry this underlying's spot price.
 * Mirrors `fno_service.rs::map_spot_quote_symbol` plus the bare name, because
 * ticks may be stored under the Kite-style symbol or the NFO name.
 */
export function spotSymbolCandidates(underlying: string): string[] {
  const names = underlyingCandidates(underlying);
  const out = new Set<string>();
  for (const n of names) {
    out.add(`NSE:${n}`);
    out.add(n);
  }
  return [...out];
}

/**
 * Reject anything that is not a plausible instrument name before it reaches a SQL
 * string literal.
 *
 * These values originate in symbol-search results, but `fno_*` is reachable from
 * any caller, and the query is assembled as text (QuestDB's REST `/exec` takes a
 * statement, not bind parameters — so there is no placeholder to fall back on).
 * An allowlist at the boundary is the guard; `quote()` below is defence in depth.
 */
const SAFE_NAME = /^[A-Za-z0-9 .&-]{1,64}$/;

export function isSafeName(value: string): boolean {
  return SAFE_NAME.test(value.trim());
}

/** A SQL string literal. QuestDB escapes an embedded quote by doubling it. */
export function quote(value: string): string {
  return `'${value.replace(/'/g, "''")}'`;
}

/** `underlying IN ('NIFTY','NIFTY 50')` for the given name's variants. */
export function underlyingClause(underlying: string): string {
  const names = underlyingCandidates(underlying).filter(isSafeName);
  if (names.length === 0) throw new Error(`unsafe underlying name: ${underlying}`);
  return `underlying IN (${names.map(quote).join(',')})`;
}

/**
 * Today in IST as `YYYY-MM-DD`, comparable against the stored `expiry` strings.
 *
 * The exchange calendar is the only calendar that matters here: a user in another
 * timezone must still see the same "nearest non-expired" contract as the market
 * does, and `en-CA` is the locale that formats as ISO.
 */
export function istToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(now);
}

/**
 * `expiry >= '<today IST>'` — the SQL half of never offering a dead series.
 *
 * `expiry` is a SYMBOL holding `YYYY-MM-DD`, so a lexicographic comparison is a
 * chronological one.
 */
export function liveExpiryClause(today: string = istToday()): string {
  return `expiry >= ${quote(today)}`;
}

/**
 * The soonest expiry not yet past, or `null` when every known expiry has expired.
 *
 * This used to fall back to the latest PAST expiry, on the reasoning that a stale
 * chain is still an honest view of the last data that existed. It is not, because
 * nothing downstream distinguishes it from a live one: the workspace charted
 * RELIANCE26AUG1290CE five days after it expired, priced its nine-day-old
 * snapshot against a live spot, and rendered impossible day-changes like -156%.
 * The chart could not recover either — an expired contract is dropped from Kite's
 * NFO instrument master, so token resolution 404s and no candle will ever arrive.
 *
 * `null` lets callers say "no live chain for this underlying", which is the true
 * statement. Every caller already handles it: `useFnoAutoContract` leaves the
 * charted symbol alone and `FnoSidebarPanel` empties the expiry selector.
 */
export function nearestExpiry(expiries: string[], today: string = istToday()): string | null {
  const sorted = [...new Set(expiries.filter((e) => e))].sort();
  return sorted.find((e) => e >= today) ?? null;
}

/**
 * The listed strike closest to `spot`; on an exact tie the lower strike.
 *
 * Port of the property-tested `services/option_chain.rs::select_atm`, tie-break
 * included — the two must agree or desktop and web would chart different
 * contracts for the same underlying.
 */
export function selectAtm(strikes: number[], spot: number): number | null {
  if (!Number.isFinite(spot)) return null;
  let best: number | null = null;
  let bestDist = Infinity;
  for (const strike of strikes) {
    if (!Number.isFinite(strike)) continue;
    const dist = Math.abs(strike - spot);
    if (best === null || dist < bestDist || (dist === bestDist && strike < best)) {
      best = strike;
      bestDist = dist;
    }
  }
  return best;
}

/** One snapshot row: a single tradable contract at the latest snapshot. */
export interface ChainRow {
  strike: number;
  optionType: 'CE' | 'PE';
  symbol: string;
  oi: number;
}

/** What `fno_resolve_*` returns. Matches `commands/fno.rs::ResolvedContract`. */
export interface ResolvedContract {
  tradingsymbol: string;
  underlying: string;
  expiry: string;
  strike: number;
  option_type: string;
}

/**
 * Walk out from the ATM strike (ATM, ±1, ±2) and take the first listed contract,
 * preferring CE.
 *
 * Mirrors `commands/fno.rs::fno_resolve_nearest_contract` steps 4-5, including
 * the OI tie-break: when both sides are listed at the candidate strike, CE wins
 * unless CE has no open interest and PE does. A CE with zero OI is untradable, so
 * charting it would show a flat line where a live PE was available.
 */
export function pickContract(rows: ChainRow[], atm: number): ChainRow | null {
  const strikes = [...new Set(rows.map((r) => r.strike))].sort((a, b) => a - b);
  if (strikes.length === 0) return null;

  const atmIndex = strikes.indexOf(atm);
  const start = atmIndex >= 0 ? atmIndex : Math.floor(strikes.length / 2);

  // ATM first, then alternating outward — the same order the Rust walk uses.
  const order: number[] = [start];
  for (let offset = 1; offset <= 2; offset += 1) {
    if (start + offset < strikes.length) order.push(start + offset);
    if (start - offset >= 0) order.push(start - offset);
  }

  for (const idx of order) {
    const strike = strikes[idx];
    const ce = rows.find((r) => r.strike === strike && r.optionType === 'CE');
    const pe = rows.find((r) => r.strike === strike && r.optionType === 'PE');
    if (ce && pe) return ce.oi > 0 || pe.oi === 0 ? ce : pe;
    if (ce) return ce;
    if (pe) return pe;
  }
  return null;
}
