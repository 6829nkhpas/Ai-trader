/**
 * SKU model — the TERMINAL / RESEARCH product split.
 *
 * WHY THIS EXISTS
 * ---------------
 * Under the SEBI Research Analyst regulations, publishing a directional
 * recommendation (buy/sell, entry, target, stop) is a regulated activity that
 * requires registration. Publishing market *analytics* — charts, indicators,
 * regime classification, volume profile, validating arithmetic on numbers the
 * user supplied themselves — is not.
 *
 * The product therefore splits into two SKUs:
 *
 *   TERMINAL  Unregulated analytics. Charts, Ghost Lines, footprint, volume
 *             profile, indicators, regime, VWEPR, S/R, patterns, and the VERIFY
 *             validator. Sellable today, no registration required.
 *
 *   RESEARCH  Regulated research output. FIND, DEBATE, conviction score, the
 *             journal, and Q&A. Requires the RA registration (INH) and a KYC'd
 *             subscriber.
 *
 * Nothing here removes a feature. It gates who may reach the recommendation
 * surface. See `docs/business/PLAN_OF_ACTION.md` §4.2 blocker P1.
 *
 * DESIGN CONSTRAINTS
 * ------------------
 * 1. Every function in this module is PURE and reads no environment variables.
 *    `computeFeatureAccess` in `./featureFlags` deliberately unlocks everything
 *    when `!IS_PROD`, which is fine for premium UI polish but would make a
 *    compliance gate untestable and unprovable. Enforcement state is a separate,
 *    explicitly-passed input — see `skuEnforcementEnabled()`.
 *
 * 2. Resolution FAILS CLOSED. Absent, null, or malformed entitlement data
 *    yields TERMINAL, never RESEARCH.
 *
 * 3. This module is defence in depth and a UX affordance ONLY. It runs on the
 *    user's machine and is therefore not a security boundary. The authoritative
 *    gate is server-side in `agents/deep-quant-loop/entitlements.py`, which the
 *    user cannot bypass. Never treat a client-side pass as authorisation.
 */

import type { AccessFlags } from './api/types';

/** The two product SKUs. Ordered: RESEARCH is a strict superset of TERMINAL. */
export type Sku = 'TERMINAL' | 'RESEARCH';

/**
 * Agent modes accepted by the Deep Quant service.
 *
 * FIND / VERIFY are user-selectable in `DeepQuantPanel`. QA is reached through
 * the separate `ask_trade_question` IPC command. DEBATE is routable on the
 * FastAPI `/run` endpoint (`graph.py` `DEBATE_MODE`) even though no UI currently
 * offers it — which is exactly why the gate must live at the API layer and not
 * only in the UI.
 */
export type AgentMode = 'FIND' | 'VERIFY' | 'DEBATE' | 'QA';

export const AGENT_MODES: readonly AgentMode[] = ['FIND', 'VERIFY', 'DEBATE', 'QA'];

/**
 * The SKU each agent mode requires.
 *
 * VERIFY is TERMINAL because it does not originate a view: the user supplies
 * their own entry, stop and target, and VERIFY checks the arithmetic against
 * ATR and the reward-to-risk floor. Validating a user's own numbers is not
 * research. Every other mode produces or elaborates a directional
 * recommendation, so all three are RESEARCH.
 */
export const AGENT_MODE_SKU: Readonly<Record<AgentMode, Sku>> = {
  FIND: 'RESEARCH',
  DEBATE: 'RESEARCH',
  QA: 'RESEARCH',
  VERIFY: 'TERMINAL',
};

/**
 * Non-mode capabilities that are also RESEARCH-only, for gating UI surfaces
 * that are not agent invocations (a rendered conviction score, the journal).
 */
export type ResearchCapability = 'convictionScore' | 'journal' | 'recommendationHistory';

export const RESEARCH_CAPABILITIES: readonly ResearchCapability[] = [
  'convictionScore',
  'journal',
  'recommendationHistory',
];

/**
 * Resolve the SKU from the user's plan entitlement flags.
 *
 * Fails closed: null/undefined flags, or a missing `canAccessResearch`, resolve
 * to TERMINAL. Only an explicit boolean `true` grants RESEARCH — a truthy string
 * such as `"false"` from a loosely-typed API response must not grant it, so this
 * checks identity against `true` rather than coercing.
 */
export function resolveSku(accessFlags: AccessFlags | null | undefined): Sku {
  if (!accessFlags || typeof accessFlags !== 'object') return 'TERMINAL';
  return accessFlags.canAccessResearch === true ? 'RESEARCH' : 'TERMINAL';
}

/** True when `sku` is permitted to run `mode`. */
export function isModeAllowed(sku: Sku, mode: AgentMode): boolean {
  // Membership is checked against the AGENT_MODES list, NOT by indexing
  // AGENT_MODE_SKU. A bare index walks the prototype chain, so a caller-supplied
  // string like "constructor" or "toString" would return a truthy value and slip
  // past an `if (!required)` guard. Modes arrive from untyped IPC payloads, so
  // this is a reachable path, not a theoretical one.
  if (!(AGENT_MODES as readonly string[]).includes(mode)) return false;
  const required = AGENT_MODE_SKU[mode];
  return required === 'TERMINAL' || sku === 'RESEARCH';
}

/** True when `sku` is permitted to use `capability`. */
export function isCapabilityAllowed(sku: Sku, capability: ResearchCapability): boolean {
  if (!RESEARCH_CAPABILITIES.includes(capability)) return false;
  return sku === 'RESEARCH';
}

/**
 * Normalise an arbitrary caller-supplied mode string to a known `AgentMode`.
 * Returns null for anything unrecognised so callers can refuse rather than
 * silently defaulting to FIND (which would be a RESEARCH bypass via a typo).
 */
export function normaliseMode(raw: string | null | undefined): AgentMode | null {
  if (typeof raw !== 'string') return null;
  const upper = raw.trim().toUpperCase();
  return (AGENT_MODES as readonly string[]).includes(upper) ? (upper as AgentMode) : null;
}

/**
 * Whether SKU enforcement is active in this build.
 *
 * Separate from the pure functions above so the gate can be exercised in dev
 * without shipping a build where the gate is silently off. Enforced in prod
 * automatically; `NEXT_PUBLIC_SKU_ENFORCE=true` turns it on locally.
 *
 * Read `IS_PROD` lazily rather than importing it at module scope: `lib/env.ts`
 * throws when `NEXT_PUBLIC_API_BASE_URL` is unset, and the pure exports of this
 * module must stay importable in a bare unit-test environment.
 */
export function skuEnforcementEnabled(): boolean {
  return (
    process.env.NEXT_PUBLIC_PROD === 'true' ||
    process.env.NEXT_PUBLIC_SKU_ENFORCE === 'true'
  );
}

/** Human-readable reason for a refusal, shown in the locked-state UI. */
export const RESEARCH_LOCKED_MESSAGE =
  'This is part of the RESEARCH plan. Trade analysis and recommendations are ' +
  'available to subscribers of our SEBI-registered research service.';

/**
 * Single decision helper for call sites: may this user run this mode right now?
 *
 * Returns a discriminated result rather than a bare boolean so callers can
 * surface the reason without re-deriving it.
 */
export type ModeGateResult =
  | { allowed: true }
  | { allowed: false; reason: 'unknown-mode' | 'requires-research'; message: string };

export function checkModeGate(
  accessFlags: AccessFlags | null | undefined,
  rawMode: string | null | undefined,
  enforced: boolean = skuEnforcementEnabled(),
): ModeGateResult {
  const mode = normaliseMode(rawMode);
  if (!mode) {
    return {
      allowed: false,
      reason: 'unknown-mode',
      message: `Unrecognised analysis mode: ${String(rawMode)}`,
    };
  }
  if (!enforced) return { allowed: true };
  if (!isModeAllowed(resolveSku(accessFlags), mode)) {
    return { allowed: false, reason: 'requires-research', message: RESEARCH_LOCKED_MESSAGE };
  }
  return { allowed: true };
}
