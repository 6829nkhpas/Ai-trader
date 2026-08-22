// Guards the enforcement scope of the deep-quant proxy and the server-side
// resolution of the feature kill switches.
//
// The scoping is the security-relevant part, and it cuts both ways:
//   • too narrow → a client that patches its own `access` map reaches the LLM
//     agent for free.
//   • too wide → gating `/options/snapshot`, which the same FastAPI app serves,
//     breaks the F&O workspace for every user the moment an operator forgets
//     `ENABLE_DEEPSEEK_GLM`. That panel has no feature gate in the UI, so a 403
//     there is a pure outage with no upgrade path shown.
//
// `assertFeatureEnabled` is tested through the real `process.env`, because that
// is what it reads in production. `vi.stubEnv` restores between cases.
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  assertFeatureEnabled,
  enforcementEnabled,
  envSwitchOn,
  featureEnabled,
  resolveFeatureConfig,
} from '../_featureSwitches';
import { isAgentPath, isStreamingPath } from '../deepquant/[...path]/route';
import { FEATURE_IDS } from '../../../lib/featureFlags';

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('deep-quant enforcement scope', () => {
  const AGENT_PATHS = [
    ['run'],
    ['qa'],
    ['resume'],
    ['cancel'],
    ['stream', 'thread-abc'],
    ['RUN'], // case-insensitive: the segment comes from a URL
  ];

  const UNGATED_PATHS = [
    ['options', 'snapshot'],
    ['options', 'chains'],
    ['options', 'expiries'],
    ['health'],
    [],
  ];

  for (const segments of AGENT_PATHS) {
    it(`gates /${segments.join('/')} on the deepseekGlm switch`, () => {
      expect(isAgentPath(segments)).toBe(true);
    });
  }

  for (const segments of UNGATED_PATHS) {
    it(`leaves /${segments.join('/') || '(root)'} ungated`, () => {
      expect(isAgentPath(segments)).toBe(false);
    });
  }

  it('streams every agent path except cancel', () => {
    // `cancel` is a one-shot POST; the rest hold the connection open for SSE.
    expect(isStreamingPath(['run'])).toBe(true);
    expect(isStreamingPath(['qa'])).toBe(true);
    expect(isStreamingPath(['resume'])).toBe(true);
    expect(isStreamingPath(['stream', 'x'])).toBe(true);
    expect(isStreamingPath(['cancel'])).toBe(false);
    expect(isStreamingPath(['options', 'snapshot'])).toBe(false);
  });

  it('never streams a path it does not also gate, except options', () => {
    // Sanity check on the two predicates staying in step: anything streamed is
    // part of the agent lifecycle.
    for (const segments of [['run'], ['qa'], ['resume'], ['stream', 'x']]) {
      expect(isStreamingPath(segments) && isAgentPath(segments)).toBe(true);
    }
  });
});

describe('envSwitchOn', () => {
  it('accepts the documented truthy spellings, case-insensitively', () => {
    for (const raw of ['true', 'TRUE', '1', 'yes', 'YES', 'on', 'On', ' true ']) {
      expect(envSwitchOn(raw), raw).toBe(true);
    }
  });

  it('treats everything else as off, including near-misses', () => {
    for (const raw of [undefined, '', ' ', 'false', '0', 'no', 'off', 'enabled', 'TRUE!']) {
      expect(envSwitchOn(raw), String(raw)).toBe(false);
    }
  });
});

describe('assertFeatureEnabled', () => {
  it('allows everything when the deployment does not enforce', () => {
    vi.stubEnv('FEATURE_ENFORCEMENT', '');
    expect(enforcementEnabled()).toBe(false);
    for (const id of FEATURE_IDS) {
      expect(featureEnabled(id), id).toBe(true);
      expect(assertFeatureEnabled(id, id), id).toBeNull();
    }
  });

  it('denies with 403 when enforcing and the switch is off', async () => {
    vi.stubEnv('FEATURE_ENFORCEMENT', 'true');
    vi.stubEnv('ENABLE_DEEPSEEK_GLM', '');

    const denied = assertFeatureEnabled('deepseekGlm', 'Deep Quant AI analysis');
    expect(denied).not.toBeNull();
    expect(denied!.status).toBe(403);

    const body = (await denied!.json()) as { error: string };
    // The message must name the feature and say who controls the switch — a bare
    // 403 on an SSE endpoint is otherwise indistinguishable from a crash.
    expect(body.error).toContain('Deep Quant AI analysis');
    expect(body.error).toMatch(/operator controls this switch/);
  });

  it('allows when enforcing and the switch is on', () => {
    vi.stubEnv('FEATURE_ENFORCEMENT', 'true');
    vi.stubEnv('ENABLE_DEEPSEEK_GLM', 'true');
    expect(assertFeatureEnabled('deepseekGlm', 'Deep Quant AI analysis')).toBeNull();
  });

  it('gates each feature independently', () => {
    vi.stubEnv('FEATURE_ENFORCEMENT', '1');
    vi.stubEnv('ENABLE_DEEPSEEK_GLM', 'true');
    vi.stubEnv('ENABLE_FOOTPRINT', 'false');
    expect(featureEnabled('deepseekGlm')).toBe(true);
    expect(featureEnabled('footprint')).toBe(false);
  });
});

describe('resolveFeatureConfig', () => {
  it('reports a switch for every feature id', () => {
    const config = resolveFeatureConfig();
    expect(Object.keys(config.switches).sort()).toEqual([...FEATURE_IDS].sort());
    for (const id of FEATURE_IDS) expect(typeof config.switches[id]).toBe('boolean');
    expect(typeof config.enforced).toBe('boolean');
  });

  it('reflects the environment rather than a captured constant', () => {
    // The point of the server-side move is that a restart changes the answer, so
    // nothing here may be memoised at module load.
    vi.stubEnv('FEATURE_ENFORCEMENT', 'true');
    vi.stubEnv('ENABLE_GHOSTLINE', 'false');
    expect(resolveFeatureConfig().switches.ghostline).toBe(false);

    vi.stubEnv('ENABLE_GHOSTLINE', 'true');
    expect(resolveFeatureConfig().switches.ghostline).toBe(true);
  });
});
