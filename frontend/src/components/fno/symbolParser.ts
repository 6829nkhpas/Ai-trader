/**
 * Parse an NSE/NFO symbol to extract its underlying derivative name.
 * Returns the NFO-compatible name (e.g. "NIFTY" not "NIFTY 50").
 */
export function getUnderlyingFromSymbol(symbol: string): string {
  const upper = symbol.trim().toUpperCase();
  if (!upper) return '';

  // Direct index / underlying names
  if (upper === 'NIFTY 50' || upper === 'NIFTY') return 'NIFTY';
  if (upper === 'NIFTY BANK' || upper === 'BANKNIFTY') return 'BANKNIFTY';
  if (upper === 'FINNIFTY' || upper === 'NIFTY FIN SERVICE') return 'FINNIFTY';
  if (upper === 'MIDCPNIFTY' || upper === 'NIFTY MIDCAP SELECT') return 'MIDCPNIFTY';

  // Options contract symbols like NIFTY2670724000CE or RELIANCE24DEC2500PE
  const match = upper.match(/^([A-Z]+)\d/);
  if (match) {
    const prefix = match[1];
    if (prefix === 'NIFTY') return 'NIFTY';
    if (prefix === 'BANKNIFTY') return 'BANKNIFTY';
    if (prefix === 'FINNIFTY') return 'FINNIFTY';
    if (prefix === 'MIDCPNIFTY') return 'MIDCPNIFTY';
    return prefix; // Stock underlying like RELIANCE, TCS, etc.
  }

  return upper;
}

const MONTH_CODES = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'];
/** NFO weekly month code: 1-9 for Jan-Sep, then O / N / D. */
const WEEKLY_MONTH_CODES = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'O', 'N', 'D'];

/**
 * An NFO option tradingsymbol, split into its real parts.
 *
 * Two shapes exist and they must not be confused, because the weekly one packs
 * the expiry DAY into digits that sit directly against the strike:
 *
 *   monthly  `NIFTY26SEP24000CE`   → underlying + YY + MMM       + strike + side
 *   weekly   `NIFTY2690124000CE`   → underlying + YY + M + DD    + strike + side
 *
 * The weekly form is live, not hypothetical: NIFTY currently lists
 * `NIFTY2690124000CE` (2026-09-01) and `NIFTY2690824000CE` (2026-09-08).
 */
export interface ParsedContractSymbol {
  underlying: string;
  /** 4-digit year. */
  year: number;
  /** 1-12. */
  month: number;
  /** Day of month for weeklies; `null` for monthlies, which omit it. */
  day: number | null;
  strike: number;
  optionType: 'CE' | 'PE';
}

// `&` is in the underlying charset for M&M / M&MFIN / L&TFH. Underlyings that
// themselves contain a digit would break the split, but none are F&O-listed.
const CONTRACT_RE = /^([A-Z&]+)(\d{2})(?:([A-Z]{3})|([1-9OND])(\d{2}))(\d+)(CE|PE)$/;

/**
 * Parse an NFO option tradingsymbol. Returns `null` for anything that is not one
 * — a futures symbol, an equity ticker, or a fabricated short symbol with no
 * expiry segment (`BANKNIFTY57000CE`).
 */
export function parseContractSymbol(symbol: string): ParsedContractSymbol | null {
  const match = symbol.trim().toUpperCase().match(CONTRACT_RE);
  if (!match) return null;
  const [, underlying, yy, monthly, weeklyMonth, weeklyDay, strike, optionType] = match;
  const month = monthly
    ? MONTH_CODES.indexOf(monthly) + 1
    : WEEKLY_MONTH_CODES.indexOf(weeklyMonth) + 1;
  if (month === 0) return null; // 3 letters that are not a month name
  return {
    underlying,
    year: 2000 + Number(yy),
    month,
    day: weeklyDay ? Number(weeklyDay) : null,
    strike: Number(strike),
    optionType: optionType as 'CE' | 'PE',
  };
}

/**
 * Match a selected contract symbol's expiry against available YYYY-MM-DD expiries.
 *
 * Exact, not scored. The previous version summed points for substrings appearing
 * anywhere in the symbol, which tied on weeklies inside the same month:
 * `NIFTY2690824000CE` scored 3 against 2026-09-01 (year "26" ✓, "269" ✓, day "1"
 * absent) and so matched the FIRST September expiry instead of the 8th.
 */
export function matchExpiryFromSymbol(symbol: string, expiries: string[]): string | null {
  const parsed = parseContractSymbol(symbol);
  if (!parsed) return null;

  const inMonth = expiries.filter((exp) => {
    const [yyyy, mm] = exp.split('-');
    return Number(yyyy) === parsed.year && Number(mm) === parsed.month;
  });
  if (inMonth.length === 0) return null;
  if (parsed.day !== null) {
    return inMonth.find((exp) => Number(exp.split('-')[2]) === parsed.day) ?? null;
  }
  // A monthly symbol carries no day, and the monthly expiry is the last one in
  // its month, so the max is the only defensible pick.
  return inMonth.reduce((a, b) => (a > b ? a : b));
}

/** Extract strike price from an options contract symbol. */
export function getStrikeFromSymbol(symbol: string): number | null {
  const parsed = parseContractSymbol(symbol);
  if (parsed) return parsed.strike;
  // A trailing digit run is the strike ONLY when there is no expiry segment to
  // confuse it with — i.e. a fabricated short symbol such as `BANKNIFTY57000CE`,
  // which an older ladder wrote and which persisted preferences still restore.
  // `useFnoAutoContract` needs the intended strike to repair those. Reading the
  // tail unconditionally is what made a weekly report strike 2690124000.
  const short = symbol.trim().toUpperCase().match(/^[A-Z&]+(\d+)(CE|PE)$/);
  return short ? Number(short[1]) : null;
}

/** Extract option side (CE/PE) from an options contract symbol. */
export function getOptionTypeFromSymbol(symbol: string): 'CE' | 'PE' | null {
  const upper = symbol.trim().toUpperCase();
  if (upper.endsWith('CE')) return 'CE';
  if (upper.endsWith('PE')) return 'PE';
  return null;
}
