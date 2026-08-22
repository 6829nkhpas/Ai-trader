// Tests for the browser's F&O chain math (`lib/bridge/fnoWeb.ts`).
//
// These are the parts that decide WHICH contract the chart shows, so a divergence
// from the Rust originals would silently chart the wrong instrument rather than
// fail. Each test names the Rust function it is pinned against.

import { describe, expect, it } from 'vitest';

import {
  isSafeName,
  istToday,
  nearestExpiry,
  pickContract,
  quote,
  selectAtm,
  spotSymbolCandidates,
  underlyingCandidates,
  underlyingClause,
  type ChainRow,
} from '../fnoWeb';

function row(strike: number, optionType: 'CE' | 'PE', oi: number): ChainRow {
  return { strike, optionType, symbol: `SYM${strike}${optionType}`, oi };
}

describe('underlying name reconciliation', () => {
  it('offers both the NFO and spot names for indices', () => {
    // fno_service.rs::underlying_alt_name — QuestDB genuinely holds rows under
    // both spellings (the live table has `NIFTY 50` AND `NIFTY`), so a query that
    // matched only one would return a partial chain.
    expect(underlyingCandidates('NIFTY')).toEqual(['NIFTY', 'NIFTY 50']);
    expect(underlyingCandidates('NIFTY 50')).toEqual(['NIFTY 50', 'NIFTY']);
    expect(underlyingCandidates('BANKNIFTY')).toEqual(['BANKNIFTY', 'NIFTY BANK']);
    expect(underlyingCandidates('nifty bank')).toEqual(['NIFTY BANK', 'BANKNIFTY']);
  });

  it('leaves a stock underlying alone', () => {
    // For single-stock derivatives the NFO underlying IS the tradingsymbol.
    expect(underlyingCandidates('RELIANCE')).toEqual(['RELIANCE']);
    expect(underlyingCandidates('  TCS  ')).toEqual(['TCS']);
  });

  it('covers both live_ticks symbol conventions for spot', () => {
    // Ticks may be stored Kite-style (`NSE:NIFTY 50`) or bare.
    const cands = spotSymbolCandidates('NIFTY');
    expect(cands).toContain('NSE:NIFTY 50');
    expect(cands).toContain('NSE:NIFTY');
    expect(cands).toContain('NIFTY');
    expect(new Set(cands).size).toBe(cands.length); // deduped
  });
});

describe('SQL literal safety', () => {
  it('rejects anything that is not a plausible instrument name', () => {
    expect(isSafeName('NIFTY 50')).toBe(true);
    expect(isSafeName('M&M')).toBe(true);
    expect(isSafeName('BAJAJ-AUTO')).toBe(true);
    expect(isSafeName("'; DROP TABLE option_chain_snapshots--")).toBe(false);
    expect(isSafeName('')).toBe(false);
    expect(isSafeName('A'.repeat(65))).toBe(false);
  });

  it('doubles an embedded quote', () => {
    expect(quote("O'NEIL")).toBe("'O''NEIL'");
  });

  it('refuses to build a clause from an unsafe name', () => {
    // The allowlist is the boundary; this asserts it actually stops the query
    // rather than emitting a sanitized-looking one.
    expect(() => underlyingClause("x'; DROP TABLE t--")).toThrow(/unsafe underlying/);
  });

  it('builds an IN clause over both name variants', () => {
    expect(underlyingClause('NIFTY')).toBe("underlying IN ('NIFTY','NIFTY 50')");
  });
});

describe('nearestExpiry', () => {
  const today = '2026-08-22';

  it('picks the soonest expiry that has not passed', () => {
    // fno_service.rs::resolve_nearest_expiry
    expect(nearestExpiry(['2026-07-14', '2026-08-27', '2026-09-24'], today)).toBe('2026-08-27');
  });

  it('treats an expiry dated today as still live', () => {
    // Expiry day IS a trading day for that contract — excluding it would move the
    // user off the instrument they are actively trading.
    expect(nearestExpiry(['2026-08-22', '2026-09-24'], today)).toBe('2026-08-22');
  });

  it('falls back to the latest past expiry when all have expired', () => {
    // A stale chain is an honest view of the last data that existed; null would
    // blank the workspace.
    expect(nearestExpiry(['2026-07-07', '2026-07-14'], today)).toBe('2026-07-14');
  });

  it('is null only when there is nothing at all', () => {
    expect(nearestExpiry([], today)).toBeNull();
    expect(nearestExpiry(['', ''], today)).toBeNull();
  });

  it('does not depend on input order or duplicates', () => {
    expect(nearestExpiry(['2026-09-24', '2026-08-27', '2026-08-27'], today)).toBe('2026-08-27');
  });
});

describe('istToday', () => {
  it('formats as an ISO date comparable against stored expiries', () => {
    expect(istToday(new Date('2026-08-22T06:00:00Z'))).toBe('2026-08-22');
  });

  it('uses the exchange calendar, not the viewer’s', () => {
    // 20:00 UTC is already the next day in IST (+05:30). A user in New York must
    // still see the same nearest contract the market does.
    expect(istToday(new Date('2026-08-22T20:00:00Z'))).toBe('2026-08-23');
  });
});

describe('selectAtm', () => {
  const strikes = [24000, 24050, 24100, 24150, 24200];

  it('picks the closest strike', () => {
    expect(selectAtm(strikes, 24138)).toBe(24150);
    expect(selectAtm(strikes, 24010)).toBe(24000);
  });

  it('keeps the LOWER strike on an exact tie', () => {
    // option_chain.rs::select_atm tie-break. Both paths must agree or desktop and
    // web would chart different contracts for the same spot.
    expect(selectAtm(strikes, 24075)).toBe(24050);
  });

  it('ignores non-finite strikes and rejects a non-finite spot', () => {
    expect(selectAtm([NaN, 24100, Infinity], 24090)).toBe(24100);
    expect(selectAtm(strikes, NaN)).toBeNull();
    expect(selectAtm([], 24000)).toBeNull();
  });
});

describe('pickContract', () => {
  it('prefers CE at the ATM strike', () => {
    const rows = [row(24100, 'CE', 5000), row(24100, 'PE', 9000)];
    expect(pickContract(rows, 24100)?.optionType).toBe('CE');
  });

  it('falls back to PE when CE has no open interest', () => {
    // commands/fno.rs step 5: a zero-OI CE is untradable, so charting it would
    // show a flat line where a live PE was available.
    const rows = [row(24100, 'CE', 0), row(24100, 'PE', 9000)];
    expect(pickContract(rows, 24100)?.optionType).toBe('PE');
  });

  it('keeps CE when neither side has open interest', () => {
    // CE preferred on a tie, including the both-zero tie.
    const rows = [row(24100, 'CE', 0), row(24100, 'PE', 0)];
    expect(pickContract(rows, 24100)?.optionType).toBe('CE');
  });

  it('widens outward when the ATM strike is not listed', () => {
    // ATM 24100 absent → the walk reaches 24150 (start+1) before 24050.
    const rows = [row(24050, 'CE', 100), row(24150, 'CE', 100)];
    expect(pickContract(rows, 24100)?.strike).toBe(24150);
  });

  it('takes the only listed side when just one exists', () => {
    expect(pickContract([row(24100, 'PE', 0)], 24100)?.optionType).toBe('PE');
  });

  it('is null on an empty chain', () => {
    expect(pickContract([], 24100)).toBeNull();
  });
});
