/**
 * Shared symbol-classification helpers used by the chart, watchlist, and
 * symbol-search flows so they all agree on what counts as an F&O tradingsymbol.
 */

/** Whether `symbol` looks like an NFO tradingsymbol (CE / PE / FUT contract). */
export function isFnoSymbol(symbol: string): boolean {
  const upper = symbol?.trim()?.toUpperCase();
  if (!upper) return false;
  if (upper.endsWith('FUT')) return true;
  if (upper.endsWith('CE') || upper.endsWith('PE')) return /\d/.test(upper);
  return false;
}
