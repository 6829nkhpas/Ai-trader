// Why SENSEX and most other indices never showed up as indices.
//
// `isIndex` decided what the search modal's "Index" tab contains. It compared the
// symbol against nine hardcoded names — NIFTY, BANKNIFTY, FINNIFTY, SENSEX,
// MIDCPNIFTY and a few spellings of the first two. The instrument masters
// actually publish 209 rows in Kite's `INDICES` segment: 136 on NSE and 73 on
// BSE. So NIFTY IT, NIFTY MIDCAP 100, NIFTY FIN SERVICE, BANKEX and roughly two
// hundred others were classified as ordinary stocks and disappeared from the tab.
//
// Kite already answers the question authoritatively with `segment`, so that is
// what decides it now, and the name list survives only as a fallback for results
// that carry no segment (desktop's `search_instruments`, and F&O rows whose
// underlying has no segment of its own).
import { describe, expect, it } from 'vitest';

import { isIndex } from '../useSymbolSearch';
import type { SearchResult } from '../../../types/searchResult';

const eq = (symbol: string, segment?: string): SearchResult => ({
  kind: 'EQ',
  symbol,
  name: symbol,
  exchange: segment === 'BSE' || symbol === 'SENSEX' ? 'BSE' : 'NSE',
  segment,
});

describe('isIndex — driven by Kite’s segment, not a name list', () => {
  it('recognises every index the masters publish, not just the famous few', () => {
    // None of these five is in the old hardcoded list; all are real INDICES rows.
    for (const symbol of ['NIFTY IT', 'NIFTY MIDCAP 100', 'NIFTY FIN SERVICE', 'BSE100', 'AUTO']) {
      expect(isIndex(eq(symbol, 'INDICES'))).toBe(true);
    }
  });

  it('recognises SENSEX and BANKEX, which live on BSE', () => {
    expect(isIndex(eq('SENSEX', 'INDICES'))).toBe(true);
    expect(isIndex(eq('BANKEX', 'INDICES'))).toBe(true);
  });

  it('does NOT mistake an index-tracking ETF for the index', () => {
    // These share the index's prefix and are ordinary equities. The name list
    // never caught them either, but the segment makes it structural rather than
    // luck: they are in the NSE/BSE segment, not INDICES.
    for (const symbol of ['NIFTYETF', 'NIFTYBEES', 'SENSEXETF', 'HDFCSENSEX', 'SENSEXBEES']) {
      expect(isIndex(eq(symbol, 'NSE'))).toBe(false);
    }
  });

  it('treats a plain scrip as a stock', () => {
    expect(isIndex(eq('RELIANCE', 'NSE'))).toBe(false);
    expect(isIndex(eq('HDFCBANK', 'NSE'))).toBe(false);
  });

  it('falls back to the name list when no segment is present', () => {
    // Desktop's search predates the field; a result without it must still work.
    expect(isIndex(eq('NIFTY 50'))).toBe(true);
    expect(isIndex(eq('SENSEX'))).toBe(true);
    expect(isIndex(eq('RELIANCE'))).toBe(false);
  });

  it('classifies an F&O contract by its underlying', () => {
    const fno = (underlying: string): SearchResult => ({
      kind: 'FNO',
      tradingsymbol: `${underlying}26SEP24000CE`,
      underlying,
      expiry: '2026-09-24',
      strike: 24000,
      optionType: 'CE',
    });
    expect(isIndex(fno('NIFTY'))).toBe(true);
    expect(isIndex(fno('BANKNIFTY'))).toBe(true);
    expect(isIndex(fno('RELIANCE'))).toBe(false);
  });
});
