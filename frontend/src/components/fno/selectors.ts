/**
 * F&O Frontend Section (F4) — pure selector helpers for FnoSection (task 8.1).
 *
 * These are the pure, total option-derivation functions that back the
 * `Underlying_Selector` and `Expiry_Selector` in `FnoSection`. They are split
 * out of the component so they can be unit/property tested in isolation without
 * mounting the React tree or pulling in the Tauri / chart dependencies.
 *
 * Behaviour mirrors the inline `useMemo` derivations that previously lived in
 * `FnoSection` verbatim — extracting them changes nothing about what the
 * selectors offer.
 *
 * Scope: presentation shaping only. No analytics, no I/O.
 */

import type { FnoChains } from './viewModel';

/**
 * deriveUnderlyingOptions — the option list offered by the `Underlying_Selector`.
 *
 * The bridge bounds `chains.underlyings` to the configured index underlyings
 * established by F1 (NIFTY 50, BANKNIFTY, …), so the selector can never offer
 * an unconfigured underlying from the chain data (R2.2, R9.3).
 *
 * Defensive guarantee: the currently-selected underlying is always selectable,
 * even before `fno_list_chains` resolves (`chains === null`) or if it is
 * momentarily absent from the configured list — it is prepended rather than
 * dropped, so the active selection never dangles. Note: `fnoUnderlying` defaults to empty and is derived from the active chart symbol via `getUnderlyingFromSymbol`.
 *
 * Pure, total, deterministic.
 */
const DEFAULT_UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY', 'MIDCPNIFTY', 'BANKEX'];

export function deriveUnderlyingOptions(
  chains: FnoChains | null,
  selectedUnderlying?: string,
): string[] {
  const chainList = Array.isArray(chains?.underlyings) ? chains!.underlyings : [];
  const combined = Array.from(new Set([...DEFAULT_UNDERLYINGS, ...chainList]));
  if (selectedUnderlying && !combined.includes(selectedUnderlying)) {
    return [selectedUnderlying, ...combined];
  }
  return combined;
}

/**
 * deriveExpiryOptions — the option list offered by the `Expiry_Selector`.
 *
 * Exactly the available expiries for the selected underlying as published by
 * the bridge (`chains.expiries_by_underlying[selectedUnderlying]`), or an empty
 * list when the underlying has no published expiries / chains have not resolved
 * (R2.2). Never synthesizes an expiry.
 *
 * Pure, total, deterministic.
 */
export function deriveExpiryOptions(
  chains: FnoChains | null,
  selectedUnderlying: string,
): string[] {
  const expiries = chains?.expiries_by_underlying?.[selectedUnderlying];
  return Array.isArray(expiries) ? expiries : [];
}
