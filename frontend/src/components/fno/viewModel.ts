/**
 * F&O Frontend Section (F4) — pure view-model layer types.
 *
 * This module declares the IPC payload shapes delivered by the Rust bridge
 * (proxying the deep-quant `GET /options/snapshot` endpoint) and the
 * chart/HUD-ready view-model structures produced by the pure selectors.
 *
 * Scope: TYPE DECLARATIONS ONLY (task 4.1). The selector function
 * implementations (`buildOiProfile`, `buildIvSkew`, `buildHudModel`,
 * `toFnoViewState`) are added in later tasks (4.2 / 4.4 / 4.7 / 4.9).
 *
 * Conventions (from the design's Data Models section):
 * - Every numeric leaf is `finite | null`; `null` is rendered as an explicit
 *   "N/A", never as `0`. The selectors never emit `NaN`/`±Infinity`.
 * - The frontend computes no options analytic; these types mirror the
 *   F1/F2/F3 output verbatim.
 */

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

/** `null` is rendered as an explicit "N/A", never as 0 (R8.2). */
export type NaOr<T> = T | null;

/** Whether the rendered chain is the symbol's own chain or a broad-market benchmark (R5.4). */
export type ChainContext = 'own-chain' | 'broad-market';

/** Agent options-bias state from F3 (R5.2). */
export type OptionsBiasState = 'bullish' | 'bearish' | 'neutral';

/** Market session status driving the live / most-recent label (R8.4). */
export type MarketStatus = 'open' | 'closed';

// ---------------------------------------------------------------------------
// IPC payload (bridge -> frontend)
// ---------------------------------------------------------------------------

/** One row per strike PRESENT in the F1 snapshot (R3.5). `iv` is finite-or-null (from F2 per_strike). */
export interface FnoChainRow {
  strike: number;
  ce_oi: NaOr<number>;
  pe_oi: NaOr<number>;
  ce_price: NaOr<number>;
  pe_price: NaOr<number>;
  iv: NaOr<number>;
}

/** F2 OI-buildup classification per side. */
export interface FnoOiBuildup {
  call: NaOr<string>;
  put: NaOr<string>;
}

/** F2 IV-skew summary; every numeric leaf finite-or-null. */
export interface FnoIvSkew {
  put_minus_call: NaOr<number>;
  slope: NaOr<number>;
  atm_iv: NaOr<number>;
}

/** F2 OI-wall support/resistance strikes; finite-or-null. */
export interface FnoOiWalls {
  support: NaOr<number>;
  resistance: NaOr<number>;
}

/** F2 `Options_Analytics_Result` (verbatim); every numeric leaf finite-or-null. */
export interface FnoAnalytics {
  spot: NaOr<number>;
  pcr_oi: NaOr<number>;
  pcr_volume: NaOr<number>;
  max_pain: NaOr<number>;
  oi_buildup: FnoOiBuildup;
  iv_skew: FnoIvSkew;
  oi_walls: FnoOiWalls;
  futures_basis: NaOr<number>;
}

/** F3 `Options_Bias` (verbatim); driving signals from the F3 output. */
export interface FnoBias {
  options_bias_state?: OptionsBiasState; // OMITTED when unavailable
  alignment?: NaOr<string>;
  chain_context?: ChainContext; // own-chain | broad-market (R5.4)
  signals?: Record<string, unknown>;
}

/** The combined IPC payload assembled from F1 (chain), F2 (analytics), F3 (bias). */
export interface FnoPayload {
  underlying: string;
  expiry: string;
  snapshot_ts: number; // epoch ms of the latest F1 snapshot
  market_status: MarketStatus; // drives the live / most-recent label (R8.4)
  chain: FnoChainRow[]; // one row per strike present in the F1 snapshot (R3.5)
  analytics: FnoAnalytics; // F2 result, verbatim
  bias: FnoBias; // F3 bias, verbatim
}

/** Honest empty marker emitted by the bridge when no snapshot/analytic exists. */
export interface FnoUnavailableMarker {
  underlying: string;
  expiry: string;
  unavailable: true;
  reason: string;
  last_snapshot_ts?: number; // present only when a prior snapshot exists (R8.4)
}

/**
 * Selector list returned by `fno_list_chains`: the configured index underlyings
 * (F1-bounded) and the available expiries per underlying (R2.2, R9.3).
 */
export interface FnoChains {
  underlyings: string[];
  expiries_by_underlying: Record<string, string[]>;
}

// ---------------------------------------------------------------------------
// Frontend view-models (output of the pure selectors)
// ---------------------------------------------------------------------------

export interface OiProfilePoint {
  strike: number;
  callOi: NaOr<number>;
  putOi: NaOr<number>;
}

export interface OiProfileModel {
  points: OiProfilePoint[]; // exactly the snapshot's strikes, ascending (R3.5)
  maxPain: NaOr<number>; // price-line when non-null (R3.2)
  support: NaOr<number>; // OI-wall support line (R3.3)
  resistance: NaOr<number>; // OI-wall resistance line (R3.3)
}

export interface IvSkewPoint {
  strike: number;
  iv: number; // iv always finite here (nulls dropped)
}

export interface IvSkewModel {
  points: IvSkewPoint[]; // null-IV strikes excluded (R4.2)
  atmStrike: NaOr<number>; // ATM marker (R4.3)
}

export interface HudModel {
  pcrOi: NaOr<number>;
  pcrVolume: NaOr<number>;
  maxPain: NaOr<number>;
  aggregateOiBias: { call: NaOr<string>; put: NaOr<string> };
  walls: { support: NaOr<number>; resistance: NaOr<number> };
  ivSkew: NaOr<{ putMinusCall: NaOr<number>; slope: NaOr<number>; atmIv: NaOr<number> }>;
  futuresBasis: NaOr<number>;
  biasState: NaOr<OptionsBiasState>;
  biasSignals: NaOr<Record<string, unknown>>;
  context: { underlying: string; expiry: string; chainContext: NaOr<ChainContext> };
}

export type FnoViewState =
  | {
      kind: 'ready';
      oi: OiProfileModel;
      iv: IvSkewModel;
      hud: HudModel;
      snapshotTs: number;
      marketStatus: MarketStatus;
    }
  | {
      kind: 'partial';
      oi: OiProfileModel;
      iv: IvSkewModel;
      hud: HudModel;
      snapshotTs: number;
      marketStatus: MarketStatus;
    }
  | { kind: 'unavailable'; reason: string; lastSnapshotTs: NaOr<number> };

// ---------------------------------------------------------------------------
// Selectors (task 4.2): buildOiProfile
// ---------------------------------------------------------------------------

/**
 * Normalize a value to `finite | null`: any non-finite number (NaN/±Infinity)
 * or non-number becomes `null`, so a missing/garbage value is never surfaced
 * as `0` or a fabricated number (R8.2). Pure and total.
 */
function finiteOrNull(value: unknown): NaOr<number> {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * buildOiProfile (task 4.2 / AD-4) — convert an IPC payload into the
 * OI-Profile / Max-Pain chart model.
 *
 * Behavior (Requirements 3.1, 3.2, 3.3, 3.5):
 * - Emits exactly one `OiProfilePoint` per strike PRESENT in `payload.chain`,
 *   sorted ascending by strike. Never synthesizes strikes outside the snapshot.
 * - `callOi`/`putOi` mirror the row's `ce_oi`/`pe_oi`, preserving `null` as
 *   `null` (never substituting `0`).
 * - `maxPain`, `support`, and `resistance` surface their source analytic
 *   (`analytics.max_pain`, `analytics.oi_walls.support`,
 *   `analytics.oi_walls.resistance`) only when non-null; otherwise `null`
 *   (no fabricated level).
 *
 * Pure, total, deterministic: tolerates a missing/garbage chain or analytics
 * by treating them as empty/null rather than throwing.
 */
export function buildOiProfile(payload: FnoPayload): OiProfileModel {
  const chain = Array.isArray(payload?.chain) ? payload.chain : [];

  const points: OiProfilePoint[] = chain
    .filter(
      (row): row is FnoChainRow =>
        row != null && typeof row.strike === 'number' && Number.isFinite(row.strike),
    )
    .map((row) => ({
      strike: row.strike,
      callOi: finiteOrNull(row.ce_oi),
      putOi: finiteOrNull(row.pe_oi),
    }))
    .sort((a, b) => a.strike - b.strike);

  const analytics = payload?.analytics;
  const walls = analytics?.oi_walls;

  return {
    points,
    maxPain: finiteOrNull(analytics?.max_pain),
    support: finiteOrNull(walls?.support),
    resistance: finiteOrNull(walls?.resistance),
  };
}

// ---------------------------------------------------------------------------
// Selectors (task 4.4): buildIvSkew
// ---------------------------------------------------------------------------

/**
 * Resolve the at-the-money strike: the chain strike nearest to `spot`.
 *
 * Returns `null` (no fabricated level) when `spot` is `null` or when no valid
 * strike exists to snap to. Deterministic: ties (two strikes equidistant from
 * spot) resolve to the lower strike. Pure and total.
 */
function nearestStrikeToSpot(validStrikes: FnoChainRow[], spot: NaOr<number>): NaOr<number> {
  if (spot === null || validStrikes.length === 0) {
    return null;
  }

  let nearest = validStrikes[0].strike;
  let bestDistance = Math.abs(nearest - spot);

  for (const row of validStrikes) {
    const distance = Math.abs(row.strike - spot);
    if (distance < bestDistance || (distance === bestDistance && row.strike < nearest)) {
      nearest = row.strike;
      bestDistance = distance;
    }
  }

  return nearest;
}

/**
 * buildIvSkew (task 4.4 / AD-4) — convert an IPC payload into the IV-Skew
 * chart model.
 *
 * Behavior (Requirements 4.1, 4.2, 4.3, 4.4):
 * - Emits one `IvSkewPoint { strike, iv }` for EXACTLY the chain strikes whose
 *   `iv` is finite, sorted ascending by strike. Strikes whose `iv` is
 *   `null` / `NaN` / `±Infinity` / missing are dropped rather than plotted as a
 *   fabricated value (R4.1, R4.2).
 * - Produces an empty `points` array precisely when no strike has a finite IV;
 *   the IV view renders an `Unavailable_State` in that case (R4.4).
 * - `atmStrike` surfaces the at-the-money strike for orientation: the chain
 *   strike nearest to the analytics `spot`, present only when `spot` is
 *   non-null and at least one valid strike exists; otherwise `null` (no
 *   fabricated level) (R4.3).
 *
 * Pure, total, deterministic: tolerates a missing/garbage chain or analytics
 * by treating them as empty/null rather than throwing.
 */
export function buildIvSkew(payload: FnoPayload): IvSkewModel {
  const chain = Array.isArray(payload?.chain) ? payload.chain : [];

  const validStrikes = chain.filter(
    (row): row is FnoChainRow =>
      row != null && typeof row.strike === 'number' && Number.isFinite(row.strike),
  );

  const points: IvSkewPoint[] = validStrikes
    .map((row) => ({ strike: row.strike, iv: finiteOrNull(row.iv) }))
    .filter((point): point is IvSkewPoint => point.iv !== null)
    .sort((a, b) => a.strike - b.strike);

  return {
    points,
    atmStrike: nearestStrikeToSpot(validStrikes, finiteOrNull(payload?.analytics?.spot)),
  };
}

// ---------------------------------------------------------------------------
// Selectors (task 4.7): buildHudModel
// ---------------------------------------------------------------------------

/**
 * Normalize a value to `string | null`: any non-string (including `null`,
 * `undefined`, numbers, objects) becomes `null`, so a missing/garbage
 * classification is surfaced as the explicit N/A sentinel rather than a
 * fabricated label. Pure and total.
 */
function stringOrNull(value: unknown): NaOr<string> {
  return typeof value === 'string' ? value : null;
}

/**
 * Resolve the agent options-bias state to a valid `OptionsBiasState` or `null`.
 * Any omitted/unknown value (F3 omits the field when unavailable) maps to
 * `null` (N/A), never a fabricated state. Pure and total.
 */
function biasStateOrNull(value: unknown): NaOr<OptionsBiasState> {
  return value === 'bullish' || value === 'bearish' || value === 'neutral' ? value : null;
}

/**
 * Resolve the chain context to a valid `ChainContext` or `null`. Any
 * omitted/unknown value maps to `null` (N/A), never a fabricated context.
 * Pure and total.
 */
function chainContextOrNull(value: unknown): NaOr<ChainContext> {
  return value === 'own-chain' || value === 'broad-market' ? value : null;
}

/**
 * buildHudModel (task 4.7 / AD-3) — convert an IPC payload into the
 * Options-analytics HUD model.
 *
 * Behavior (Requirements 5.1, 5.2, 5.3, 5.4, 8.2):
 * - Exposes every headline field: PCR by OI and by volume, max pain, aggregate
 *   OI bias (call/put buildup classification), nearest OI walls
 *   (support/resistance), the IV-skew summary (put-minus-call / slope / ATM IV),
 *   and the futures basis (R5.1).
 * - Exposes the agent bias state (`bias.options_bias_state`) with its driving
 *   `bias.signals`; each is `null` (N/A) when F3 omits it (R5.2).
 * - Exposes the chain context: the underlying, the expiry, and whether the
 *   rendered chain is `own-chain` or `broad-market` (R5.4).
 * - Every numeric/string leaf maps to its source value when finite/present, or
 *   to the explicit N/A sentinel (`null`) when `null`/omitted — never to `0`,
 *   `''`, or any fabricated value (R5.3, R8.2). Numeric leaves reuse
 *   `finiteOrNull`, so `NaN`/`±Infinity` also collapse to `null`.
 * - The whole `ivSkew` summary is `null` when the source `analytics.iv_skew`
 *   object is absent; otherwise it is an object whose nested leaves are each
 *   finite-or-null.
 *
 * Pure, total, deterministic: tolerates a missing/garbage analytics or bias by
 * treating absent fields as `null` rather than throwing.
 */
export function buildHudModel(payload: FnoPayload): HudModel {
  const analytics = payload?.analytics;
  const buildup = analytics?.oi_buildup;
  const walls = analytics?.oi_walls;
  const skew = analytics?.iv_skew;
  const bias = payload?.bias;

  return {
    pcrOi: finiteOrNull(analytics?.pcr_oi),
    pcrVolume: finiteOrNull(analytics?.pcr_volume),
    maxPain: finiteOrNull(analytics?.max_pain),
    aggregateOiBias: {
      call: stringOrNull(buildup?.call),
      put: stringOrNull(buildup?.put),
    },
    walls: {
      support: finiteOrNull(walls?.support),
      resistance: finiteOrNull(walls?.resistance),
    },
    ivSkew:
      skew == null
        ? null
        : {
            putMinusCall: finiteOrNull(skew.put_minus_call),
            slope: finiteOrNull(skew.slope),
            atmIv: finiteOrNull(skew.atm_iv),
          },
    futuresBasis: finiteOrNull(analytics?.futures_basis),
    biasState: biasStateOrNull(bias?.options_bias_state),
    biasSignals:
      bias?.signals != null && typeof bias.signals === 'object'
        ? (bias.signals as Record<string, unknown>)
        : null,
    context: {
      underlying: typeof payload?.underlying === 'string' ? payload.underlying : '',
      expiry: typeof payload?.expiry === 'string' ? payload.expiry : '',
      chainContext: chainContextOrNull(bias?.chain_context),
    },
  };
}

// ---------------------------------------------------------------------------
// Selectors (task 4.9): toFnoViewState
// ---------------------------------------------------------------------------

/** Coerce a value to a non-empty human-readable reason string, or `null`. */
function nonEmptyReasonOrNull(value: unknown): NaOr<string> {
  return typeof value === 'string' && value.trim().length > 0 ? value : null;
}

/** Coerce a value to a valid `MarketStatus`. Anything other than `'open'`
 * collapses to `'closed'` so a missing/garbage status never implies live data
 * (R8.4). Pure and total. */
function marketStatusOrClosed(value: unknown): MarketStatus {
  return value === 'open' ? 'open' : 'closed';
}

/**
 * Decide whether any headline analytic leaf in the HUD model is the explicit
 * N/A sentinel (`null`). Used to tag a snapshot-bearing payload as `partial`
 * (at least one analytic field is N/A) versus `ready` (every analytic field is
 * present) per AD-5 / Property 5. Pure and total.
 */
function hudHasNullAnalytic(hud: HudModel): boolean {
  if (
    hud.pcrOi === null ||
    hud.pcrVolume === null ||
    hud.maxPain === null ||
    hud.futuresBasis === null ||
    hud.aggregateOiBias.call === null ||
    hud.aggregateOiBias.put === null ||
    hud.walls.support === null ||
    hud.walls.resistance === null ||
    hud.biasState === null ||
    hud.biasSignals === null ||
    hud.context.chainContext === null
  ) {
    return true;
  }

  // The IV-skew summary is N/A when absent, or when any nested leaf is N/A.
  if (
    hud.ivSkew === null ||
    hud.ivSkew.putMinusCall === null ||
    hud.ivSkew.slope === null ||
    hud.ivSkew.atmIv === null
  ) {
    return true;
  }

  return false;
}

/**
 * toFnoViewState (task 4.9 / AD-5) — collapse a single IPC payload (or
 * `Unavailable_Marker`, or any malformed/garbage input) into one tagged
 * `FnoViewState`. This is the honest-empty-state boundary: components branch on
 * `viewState.kind`, so a fabricated zero can never reach a chart or HUD field.
 *
 * Behavior (Requirements 6.4, 6.5, 8.1, 8.3, 8.4):
 * - Returns `{ kind: 'unavailable', reason, lastSnapshotTs }` when the input is
 *   an `Unavailable_Marker` (`unavailable === true`), when no snapshot exists
 *   (missing/empty chain or non-finite `snapshot_ts`), or when the input is not
 *   a usable object — carrying a non-empty human-readable reason (the marker's
 *   own `reason` when present, else a synthesized one) and `lastSnapshotTs`
 *   from the marker's `last_snapshot_ts` when present, else the payload's own
 *   `snapshot_ts` when finite, else `null`. No chart/HUD models are produced
 *   (Property 6).
 * - Otherwise builds the OI / IV / HUD models via the existing selectors and
 *   returns `kind: 'ready'`, or `kind: 'partial'` when at least one analytic
 *   field is `null` while a snapshot exists (Property 5 / R8.3). Both carry the
 *   payload's `snapshotTs` (`snapshot_ts`) and `marketStatus` (`market_status`,
 *   coerced so a closed/garbage status never implies live data — R8.4 /
 *   Property 7).
 *
 * TOTAL (Property 8 / R6.5): never throws on a well-formed payload, a marker, a
 * partial payload, a malformed/garbage object, non-object input, or missing
 * fields — any non-renderable input collapses to a `kind: 'unavailable'` state
 * rather than a crash. Pure and deterministic.
 */
export function toFnoViewState(payload: FnoPayload | FnoUnavailableMarker): FnoViewState {
  try {
    // Non-object / garbage input -> honest unavailable (never throw).
    if (payload === null || typeof payload !== 'object') {
      return {
        kind: 'unavailable',
        reason: 'no F&O snapshot available',
        lastSnapshotTs: null,
      };
    }

    const candidate = payload as Partial<FnoPayload> & Partial<FnoUnavailableMarker>;

    const underlying = typeof candidate.underlying === 'string' ? candidate.underlying : '';
    const expiry = typeof candidate.expiry === 'string' ? candidate.expiry : '';
    const markerLastTs = finiteOrNull(candidate.last_snapshot_ts);
    const payloadTs = finiteOrNull(candidate.snapshot_ts);

    // Explicit Unavailable_Marker (honest empty state from the bridge).
    if (candidate.unavailable === true) {
      return {
        kind: 'unavailable',
        reason:
          nonEmptyReasonOrNull(candidate.reason) ??
          `F&O analytics unavailable for ${underlying || 'the selected underlying'} / ${
            expiry || 'nearest expiry'
          }`,
        lastSnapshotTs: markerLastTs ?? payloadTs,
      };
    }

    // A snapshot exists only when the chain carries at least one row AND the
    // snapshot timestamp is a finite number; otherwise it is an empty/missing
    // chain that must collapse to an honest unavailable state (Property 6).
    const chain = Array.isArray(candidate.chain) ? candidate.chain : [];
    const hasSnapshot = chain.length > 0 && payloadTs !== null;

    if (!hasSnapshot) {
      return {
        kind: 'unavailable',
        reason: `no chain snapshot available for ${underlying || 'the selected underlying'} / ${
          expiry || 'nearest expiry'
        }`,
        lastSnapshotTs: markerLastTs ?? payloadTs,
      };
    }

    // Snapshot present: build the chart/HUD models via the existing selectors.
    const fnoPayload = candidate as FnoPayload;
    const oi = buildOiProfile(fnoPayload);
    const iv = buildIvSkew(fnoPayload);
    const hud = buildHudModel(fnoPayload);

    const snapshotTs = payloadTs as number;
    const marketStatus = marketStatusOrClosed(candidate.market_status);

    // `partial` when at least one analytic field is N/A; `ready` otherwise.
    const kind = hudHasNullAnalytic(hud) ? 'partial' : 'ready';

    return { kind, oi, iv, hud, snapshotTs, marketStatus };
  } catch {
    // Ultimate totality guard: any unforeseen failure becomes an honest empty
    // state rather than a crash (Property 8 / R6.5).
    return {
      kind: 'unavailable',
      reason: 'F&O analytics unavailable (malformed result)',
      lastSnapshotTs: null,
    };
  }
}
