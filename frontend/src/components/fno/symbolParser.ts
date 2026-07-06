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

/**
 * Match a selected contract symbol's expiry against available YYYY-MM-DD expiries.
 * Works with both monthly (NIFTY26JUL24000CE) and weekly (NIFTY2670724000CE) formats.
 */
export function matchExpiryFromSymbol(symbol: string, expiries: string[]): string | null {
  const upperSymbol = symbol.trim().toUpperCase();
  const months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

  for (const exp of expiries) {
    const parts = exp.split('-');
    if (parts.length < 3) continue;
    const yyyy = parts[0];
    const mm = parts[1];
    const dd = parts[2];
    const yy = yyyy.slice(-2);
    const monthIndex = parseInt(mm, 10) - 1;
    const mmm = months[monthIndex];

    let score = 0;
    if (upperSymbol.includes(yy)) score += 1;

    if (upperSymbol.includes(mmm)) {
      score += 2;
    } else {
      const weeklyMonthCodes = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "O", "N", "D"];
      const code = weeklyMonthCodes[monthIndex];
      if (upperSymbol.includes(yy + code)) {
        score += 2;
      }
    }

    const dayNum = parseInt(dd, 10).toString();
    if (upperSymbol.includes(dayNum)) score += 1;

    if (score >= 3) {
      return exp;
    }
  }
  return null;
}

/** Extract strike price from an options contract symbol. */
export function getStrikeFromSymbol(symbol: string): number | null {
  const upper = symbol.trim().toUpperCase();
  const match = upper.match(/(\d+)(CE|PE)$/);
  if (match) {
    return parseFloat(match[1]);
  }
  return null;
}

/** Extract option side (CE/PE) from an options contract symbol. */
export function getOptionTypeFromSymbol(symbol: string): 'CE' | 'PE' | null {
  const upper = symbol.trim().toUpperCase();
  if (upper.endsWith('CE')) return 'CE';
  if (upper.endsWith('PE')) return 'PE';
  return null;
}
