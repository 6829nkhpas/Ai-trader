// NFO symbol parsing — the weekly format is where this used to go wrong.
//
// An NFO option tradingsymbol comes in two shapes and only one of them separates
// the expiry from the strike with letters:
//
//   monthly  NIFTY26SEP24000CE   → NIFTY + 26 + SEP   + 24000 + CE
//   weekly   NIFTY2690124000CE   → NIFTY + 26 + 9 + 01 + 24000 + CE
//
// `getStrikeFromSymbol` used to read the trailing digit run (`/(\d+)(CE|PE)$/`),
// which is right for the monthly and reports 2690124000 for the weekly. That fed
// `useFnoExpiryChange`, so changing the expiry while a weekly was charted asked
// for a strike of 2.69 billion and the resolver's ATM walk landed on the highest
// strike in the chain — a chart the user never asked for.
//
// The symbols below are real: `option_chain_snapshots` currently lists
// NIFTY26AUG24000CE (2026-08-25), NIFTY2690124000CE (2026-09-01) and
// NIFTY2690824000CE (2026-09-08).
import { describe, expect, it } from 'vitest';

import {
  getStrikeFromSymbol,
  matchExpiryFromSymbol,
  parseContractSymbol,
} from '../symbolParser';

describe('parseContractSymbol', () => {
  it('splits a monthly contract', () => {
    expect(parseContractSymbol('BANKNIFTY26SEP57000CE')).toEqual({
      underlying: 'BANKNIFTY',
      year: 2026,
      month: 9,
      day: null,
      strike: 57000,
      optionType: 'CE',
    });
  });

  it('splits a weekly contract, day included', () => {
    expect(parseContractSymbol('NIFTY2690124000CE')).toEqual({
      underlying: 'NIFTY',
      year: 2026,
      month: 9,
      day: 1,
      strike: 24000,
      optionType: 'CE',
    });
  });

  it('reads the O/N/D weekly month codes', () => {
    expect(parseContractSymbol('NIFTY26O2724000PE')).toMatchObject({ month: 10, day: 27 });
    expect(parseContractSymbol('NIFTY26N2424000PE')).toMatchObject({ month: 11, day: 24 });
    expect(parseContractSymbol('NIFTY26D2924000PE')).toMatchObject({ month: 12, day: 29 });
  });

  it('rejects what is not an option contract', () => {
    expect(parseContractSymbol('RELIANCE26AUGFUT')).toBeNull();
    expect(parseContractSymbol('NIFTY 50')).toBeNull();
    expect(parseContractSymbol('HDFCBANK')).toBeNull();
    // Fabricated: no expiry segment at all.
    expect(parseContractSymbol('BANKNIFTY57000CE')).toBeNull();
    // Three letters where the month goes, but not a month.
    expect(parseContractSymbol('NIFTY26XYZ24000CE')).toBeNull();
  });
});

describe('getStrikeFromSymbol', () => {
  it('reads the strike, not the date digits, off a weekly', () => {
    expect(getStrikeFromSymbol('NIFTY2690124000CE')).toBe(24000);
    expect(getStrikeFromSymbol('NIFTY2690824500PE')).toBe(24500);
  });

  it('reads the strike off a monthly', () => {
    expect(getStrikeFromSymbol('BANKNIFTY26SEP57000CE')).toBe(57000);
    expect(getStrikeFromSymbol('RELIANCE26AUG2500PE')).toBe(2500);
  });

  it('still recovers the intended strike from a fabricated short symbol', () => {
    // `useFnoAutoContract` repairs these, and it needs the strike to do it.
    expect(getStrikeFromSymbol('BANKNIFTY57000CE')).toBe(57000);
  });

  it('is null when there is no strike to read', () => {
    expect(getStrikeFromSymbol('RELIANCE26AUGFUT')).toBeNull();
    expect(getStrikeFromSymbol('NIFTY 50')).toBeNull();
    expect(getStrikeFromSymbol('')).toBeNull();
  });
});

describe('matchExpiryFromSymbol', () => {
  const SEPT = ['2026-09-01', '2026-09-08', '2026-09-29'];

  it('picks the exact week a weekly expires', () => {
    expect(matchExpiryFromSymbol('NIFTY2690124000CE', SEPT)).toBe('2026-09-01');
    // The old scoring version answered 2026-09-01 here: it scored 3 on the year
    // and the "269" month code and never noticed the day disagreed.
    expect(matchExpiryFromSymbol('NIFTY2690824000CE', SEPT)).toBe('2026-09-08');
  });

  it('picks the last expiry of the month for a monthly, which is the monthly one', () => {
    expect(matchExpiryFromSymbol('NIFTY26SEP24000CE', SEPT)).toBe('2026-09-29');
  });

  it('is null when the symbol names an expiry that is not on offer', () => {
    expect(matchExpiryFromSymbol('NIFTY2690224000CE', SEPT)).toBeNull();
    expect(matchExpiryFromSymbol('NIFTY26AUG24000CE', SEPT)).toBeNull();
    expect(matchExpiryFromSymbol('NIFTY26SEP24000CE', [])).toBeNull();
  });

  it('is null for a symbol with no expiry to match', () => {
    expect(matchExpiryFromSymbol('BANKNIFTY57000CE', SEPT)).toBeNull();
    expect(matchExpiryFromSymbol('NIFTY 50', SEPT)).toBeNull();
  });
});
