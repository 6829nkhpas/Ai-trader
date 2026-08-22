// @vitest-environment node

// Compliance blocker P1 — SKU gate proof.
//
// Gate 0→1 in `docs/business/PLAN_OF_ACTION.md` §4.2 requires that "no
// recommendation surface [is] reachable by an unlicensed user, verified by a
// written test, not an eyeball". This file is the pure-model half of that proof;
// `store/__tests__/useQuantStore.skuGate.property.test.ts` proves the runtime
// half (that no IPC is issued).
//
// The properties asserted here are the ones a regulator's question reduces to:
//
//   1. Under TERMINAL, every RESEARCH mode is refused — for ALL inputs, not for
//      a hand-picked few.
//   2. VERIFY is never refused, because validating the user's own arithmetic is
//      not research and must keep working on the unregulated SKU.
//   3. Resolution FAILS CLOSED: nothing other than a literal boolean `true` on
//      `canAccessResearch` yields RESEARCH.
//   4. An unrecognised mode string is refused rather than silently defaulting.

import { afterEach, beforeEach, describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  AGENT_MODES,
  AGENT_MODE_SKU,
  RESEARCH_CAPABILITIES,
  checkModeGate,
  isCapabilityAllowed,
  isModeAllowed,
  normaliseMode,
  resolveSku,
  skuEnforcementEnabled,
  type AgentMode,
  type ResearchCapability,
  type Sku,
} from '@/lib/sku';
import type { AccessFlags } from '@/lib/api/types';

const RUNS = 300;

// ── Arbitraries ──────────────────────────────────────────────────────────

const arbSku: fc.Arbitrary<Sku> = fc.constantFrom<Sku>('TERMINAL', 'RESEARCH');

const arbMode: fc.Arbitrary<AgentMode> = fc.constantFrom(...AGENT_MODES);

/** The RESEARCH-requiring modes, derived from the table rather than restated,
 *  so adding a mode to `AGENT_MODE_SKU` automatically widens the test. */
const RESEARCH_MODES = AGENT_MODES.filter((m) => AGENT_MODE_SKU[m] === 'RESEARCH');
const TERMINAL_MODES = AGENT_MODES.filter((m) => AGENT_MODE_SKU[m] === 'TERMINAL');

const arbResearchMode: fc.Arbitrary<AgentMode> = fc.constantFrom(...RESEARCH_MODES);

const arbCapability: fc.Arbitrary<ResearchCapability> = fc.constantFrom(...RESEARCH_CAPABILITIES);

/** Every shape a loosely-typed remote API could plausibly put in the
 *  `canAccessResearch` slot, EXCLUDING boolean `true`. All must deny. */
const arbNonGrantingValue = fc.oneof(
  fc.constant(false),
  fc.constant(undefined),
  fc.constant(null),
  fc.constant(0),
  fc.constant(1),
  fc.constant(''),
  fc.constant('false'),
  fc.constant('true'), // a truthy STRING must not grant — identity check, not coercion
  fc.constant('RESEARCH'),
  fc.constant([]),
  fc.constant({}),
  fc.string(),
  fc.integer(),
);

/** Arbitrary other flags, to prove no unrelated flag can leak entitlement. */
const arbOtherFlags = fc.dictionary(
  fc.string({ minLength: 1, maxLength: 12 }).filter((k) => k !== 'canAccessResearch'),
  fc.oneof(fc.boolean(), fc.string(), fc.integer()),
  { maxKeys: 6 },
);

// ── 1. Mode gating ───────────────────────────────────────────────────────

describe('P1 — RESEARCH modes are unreachable on TERMINAL', () => {
  it('refuses every RESEARCH mode under a TERMINAL SKU', () => {
    fc.assert(
      fc.property(arbResearchMode, (mode) => {
        expect(isModeAllowed('TERMINAL', mode)).toBe(false);
      }),
      { numRuns: RUNS },
    );
  });

  it('enumerates the RESEARCH modes explicitly, so the table cannot silently shrink', () => {
    // If someone reclassifies FIND/DEBATE/QA as TERMINAL, this fails loudly.
    expect([...RESEARCH_MODES].sort()).toEqual(['DEBATE', 'FIND', 'QA']);
    expect([...TERMINAL_MODES].sort()).toEqual(['VERIFY']);
  });

  it('permits VERIFY on both SKUs — validating user-supplied numbers is not research', () => {
    fc.assert(
      fc.property(arbSku, (sku) => {
        expect(isModeAllowed(sku, 'VERIFY')).toBe(true);
      }),
      { numRuns: RUNS },
    );
  });

  it('permits every mode under RESEARCH — the gate restricts, it never removes a feature', () => {
    fc.assert(
      fc.property(arbMode, (mode) => {
        expect(isModeAllowed('RESEARCH', mode)).toBe(true);
      }),
      { numRuns: RUNS },
    );
  });

  it('refuses an unknown mode rather than defaulting to a permitted one', () => {
    fc.assert(
      fc.property(
        fc.string().filter((s) => !(AGENT_MODES as readonly string[]).includes(s.trim().toUpperCase())),
        (junk) => {
          expect(normaliseMode(junk)).toBeNull();
          // Cast: deliberately probing the unknown-mode branch of a typed API.
          expect(isModeAllowed('TERMINAL', junk as AgentMode)).toBe(false);
          expect(isModeAllowed('RESEARCH', junk as AgentMode)).toBe(false);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('normalises case and surrounding whitespace without widening the accepted set', () => {
    expect(normaliseMode('  find ')).toBe('FIND');
    expect(normaliseMode('verify')).toBe('VERIFY');
    expect(normaliseMode('FIND ')).toBe('FIND');
    expect(normaliseMode('FINDER')).toBeNull();
    expect(normaliseMode('')).toBeNull();
    expect(normaliseMode(null)).toBeNull();
    expect(normaliseMode(undefined)).toBeNull();
  });
});

// ── 2. SKU resolution fails closed ───────────────────────────────────────

describe('P1 — SKU resolution fails closed', () => {
  it('resolves TERMINAL for anything that is not a literal boolean true', () => {
    fc.assert(
      fc.property(arbNonGrantingValue, arbOtherFlags, (value, others) => {
        const flags = { ...others, canAccessResearch: value } as unknown as AccessFlags;
        expect(resolveSku(flags)).toBe('TERMINAL');
      }),
      { numRuns: RUNS },
    );
  });

  it('resolves TERMINAL for null, undefined and non-object inputs', () => {
    expect(resolveSku(null)).toBe('TERMINAL');
    expect(resolveSku(undefined)).toBe('TERMINAL');
    expect(resolveSku('RESEARCH' as unknown as AccessFlags)).toBe('TERMINAL');
    expect(resolveSku(1 as unknown as AccessFlags)).toBe('TERMINAL');
    expect(resolveSku(true as unknown as AccessFlags)).toBe('TERMINAL');
  });

  it('resolves TERMINAL when the flag is simply absent', () => {
    fc.assert(
      fc.property(arbOtherFlags, (others) => {
        expect(resolveSku(others as unknown as AccessFlags)).toBe('TERMINAL');
      }),
      { numRuns: RUNS },
    );
  });

  it('resolves RESEARCH only on an explicit boolean true', () => {
    fc.assert(
      fc.property(arbOtherFlags, (others) => {
        const flags = { ...others, canAccessResearch: true } as unknown as AccessFlags;
        expect(resolveSku(flags)).toBe('RESEARCH');
      }),
      { numRuns: RUNS },
    );
  });
});

// ── 3. Non-mode capabilities ─────────────────────────────────────────────

describe('P1 — RESEARCH capabilities are unreachable on TERMINAL', () => {
  it('locks every declared capability under TERMINAL and unlocks it under RESEARCH', () => {
    fc.assert(
      fc.property(arbCapability, (cap) => {
        expect(isCapabilityAllowed('TERMINAL', cap)).toBe(false);
        expect(isCapabilityAllowed('RESEARCH', cap)).toBe(true);
      }),
      { numRuns: RUNS },
    );
  });

  it('refuses an undeclared capability on both SKUs', () => {
    fc.assert(
      fc.property(
        arbSku,
        fc.string().filter((s) => !(RESEARCH_CAPABILITIES as readonly string[]).includes(s)),
        (sku, junk) => {
          expect(isCapabilityAllowed(sku, junk as ResearchCapability)).toBe(false);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('gates the conviction score — the surface that reads as expected return', () => {
    // Feature Matrix #9: a conviction number attached to a directional call is
    // regulated research output, not neutral analytics.
    expect(RESEARCH_CAPABILITIES).toContain('convictionScore');
    expect(isCapabilityAllowed('TERMINAL', 'convictionScore')).toBe(false);
  });
});

// ── 4. The composite decision helper ─────────────────────────────────────

describe('P1 — checkModeGate', () => {
  const TERMINAL_FLAGS = { canAccessResearch: false } as unknown as AccessFlags;
  const RESEARCH_FLAGS = { canAccessResearch: true } as unknown as AccessFlags;

  it('refuses RESEARCH modes for a TERMINAL user when enforced', () => {
    fc.assert(
      fc.property(arbResearchMode, (mode) => {
        const result = checkModeGate(TERMINAL_FLAGS, mode, true);
        expect(result.allowed).toBe(false);
        if (!result.allowed) expect(result.reason).toBe('requires-research');
      }),
      { numRuns: RUNS },
    );
  });

  it('refuses RESEARCH modes for a user with no entitlement data at all', () => {
    fc.assert(
      fc.property(arbResearchMode, fc.constantFrom(null, undefined), (mode, flags) => {
        expect(checkModeGate(flags, mode, true).allowed).toBe(false);
      }),
      { numRuns: RUNS },
    );
  });

  it('allows RESEARCH modes for an entitled user when enforced', () => {
    fc.assert(
      fc.property(arbResearchMode, (mode) => {
        expect(checkModeGate(RESEARCH_FLAGS, mode, true).allowed).toBe(true);
      }),
      { numRuns: RUNS },
    );
  });

  it('allows VERIFY regardless of entitlement or enforcement', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(TERMINAL_FLAGS, RESEARCH_FLAGS, null, undefined),
        fc.boolean(),
        (flags, enforced) => {
          expect(checkModeGate(flags, 'VERIFY', enforced).allowed).toBe(true);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('refuses an unknown mode even when enforcement is OFF', () => {
    // Enforcement is a licensing switch, not an input validator. A typo must
    // never resolve to a runnable mode, or a mis-cased "reasearch" string
    // becomes a bypass the moment enforcement is turned on.
    fc.assert(
      fc.property(
        fc.string().filter((s) => !(AGENT_MODES as readonly string[]).includes(s.trim().toUpperCase())),
        fc.boolean(),
        (junk, enforced) => {
          const result = checkModeGate(RESEARCH_FLAGS, junk, enforced);
          expect(result.allowed).toBe(false);
          if (!result.allowed) expect(result.reason).toBe('unknown-mode');
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('allows known RESEARCH modes when enforcement is off (dev ergonomics)', () => {
    fc.assert(
      fc.property(arbResearchMode, (mode) => {
        expect(checkModeGate(null, mode, false).allowed).toBe(true);
      }),
      { numRuns: RUNS },
    );
  });

  it('carries the user-facing refusal message, not a bare boolean', () => {
    const result = checkModeGate(TERMINAL_FLAGS, 'FIND', true);
    expect(result.allowed).toBe(false);
    if (!result.allowed) {
      expect(result.message.length).toBeGreaterThan(0);
      // The copy must frame this as a plan boundary, never as a system fault.
      expect(result.message.toLowerCase()).toContain('research');
    }
  });
});

// ── skuEnforcementEnabled — the closed-beta opt-out ──────────────────────────
//
// This decides whether the REGULATED recommendation surface renders. It is the
// one switch in this module that is a compliance decision rather than a product
// one, so its truth table is pinned explicitly: a silent flip in either
// direction is either an outage (locked for paying users) or a regulatory
// exposure (published to the public without RA registration).

describe('skuEnforcementEnabled', () => {
  const KEYS = ['NEXT_PUBLIC_PROD', 'NEXT_PUBLIC_SKU_ENFORCE', 'NEXT_PUBLIC_RESEARCH_BETA_OPEN'] as const;
  let saved: Record<string, string | undefined>;

  beforeEach(() => {
    saved = {};
    for (const k of KEYS) {
      saved[k] = process.env[k];
      delete process.env[k];
    }
  });

  afterEach(() => {
    for (const k of KEYS) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
  });

  it('enforces in a production build', () => {
    process.env.NEXT_PUBLIC_PROD = 'true';
    expect(skuEnforcementEnabled()).toBe(true);
  });

  it('enforces when explicitly switched on for local testing', () => {
    process.env.NEXT_PUBLIC_SKU_ENFORCE = 'true';
    expect(skuEnforcementEnabled()).toBe(true);
  });

  it('does not enforce in a bare dev build', () => {
    expect(skuEnforcementEnabled()).toBe(false);
  });

  it('the beta opt-out disables the gate even in a production build', () => {
    // The closed-beta case. Deliberately overrides BOTH enforcement inputs, which
    // is why it is checked against them set rather than absent.
    process.env.NEXT_PUBLIC_PROD = 'true';
    process.env.NEXT_PUBLIC_SKU_ENFORCE = 'true';
    process.env.NEXT_PUBLIC_RESEARCH_BETA_OPEN = 'true';
    expect(skuEnforcementEnabled()).toBe(false);
  });

  it('requires the exact string "true" — anything else keeps the gate ON', () => {
    // Fail CLOSED on a typo. `RESEARCH_BETA_OPEN=1` or `=yes` must NOT open a
    // regulated surface: the other feature switches accept those spellings, and
    // someone will reasonably assume this one does too.
    process.env.NEXT_PUBLIC_PROD = 'true';
    for (const v of ['1', 'yes', 'on', 'TRUE', 'True', '', ' true ']) {
      process.env.NEXT_PUBLIC_RESEARCH_BETA_OPEN = v;
      expect(skuEnforcementEnabled(), `value ${JSON.stringify(v)} must not open the gate`).toBe(true);
    }
  });
});
