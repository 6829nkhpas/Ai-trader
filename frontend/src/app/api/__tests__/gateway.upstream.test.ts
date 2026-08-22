// Tests for upstream URL resolution (`app/api/_gateway.ts::upstreamBase`).
//
// These exist because of a bug that reached production: `/api/tools/get_candles`
// returned 404 while both the frontend and tool-server were healthy, the route
// was registered in the build manifest, and the identical image behaved correctly
// under a different env. The cause was a missing path prefix — tool-server mounts
// everything under `/tools`, the `[...path]` segment arrives with that prefix
// stripped, and the override branch did not restore it. So the proxy asked for
// `http://tool-server:8084/get_candles`, the upstream 404'd, and the proxy passed
// that through verbatim — indistinguishable from a missing route.
//
// The lesson these tests encode: a prefix has to be applied on EVERY branch of the
// resolution ladder, not just the gateway one. A per-service override is the path
// production actually takes (docker-compose sets all five), so it is the branch
// most worth pinning and was the one that was wrong.

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { upstreamBase } from '../_gateway';

const KEYS = [
  'STRATAI_HTTP_BASE_URL',
  'STRATAI_SERVER_HOST',
  'KITE_API_URL',
  'QUESTDB_HTTP_URL',
  'DEEP_QUANT_URL',
  'QUANT_TOOL_SERVER_URL',
  'SENTIMENT_HTTP_URL',
] as const;

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

describe('tools upstream carries the /tools prefix on every branch', () => {
  it('appends /tools to a per-service override', () => {
    // The production path: docker-compose.prod.yml sets exactly this value.
    process.env.QUANT_TOOL_SERVER_URL = 'http://tool-server:8084';
    expect(upstreamBase('tools')).toBe('http://tool-server:8084/tools');
  });

  it('does not double /tools when the operator already included it', () => {
    process.env.QUANT_TOOL_SERVER_URL = 'http://tool-server:8084/tools';
    expect(upstreamBase('tools')).toBe('http://tool-server:8084/tools');
  });

  it('tolerates a trailing slash on either form', () => {
    process.env.QUANT_TOOL_SERVER_URL = 'http://tool-server:8084/';
    expect(upstreamBase('tools')).toBe('http://tool-server:8084/tools');
    process.env.QUANT_TOOL_SERVER_URL = 'http://tool-server:8084/tools/';
    expect(upstreamBase('tools')).toBe('http://tool-server:8084/tools');
  });

  it('appends /tools on the gateway branch', () => {
    process.env.STRATAI_HTTP_BASE_URL = 'https://app-api.stratai.live';
    expect(upstreamBase('tools')).toBe('https://app-api.stratai.live/tools');
  });

  it('appends /tools on the localhost fallback', () => {
    // The branch a bare `npm run dev` takes with nothing configured.
    expect(upstreamBase('tools')).toBe('http://127.0.0.1:8084/tools');
  });
});

describe('the other upstreams keep their own conventions', () => {
  it('kite carries /api/kite, which the override already supplies', () => {
    // Unlike tools, the aggregator's prefix lives IN the configured value —
    // which is why kite worked in production while tools did not.
    process.env.KITE_API_URL = 'http://aggregator:8087/api/kite';
    expect(upstreamBase('kite')).toBe('http://aggregator:8087/api/kite');
  });

  it('kite falls back to the full /api/kite path', () => {
    expect(upstreamBase('kite')).toBe('http://127.0.0.1:8087/api/kite');
  });

  it('questdb is served at the root, so it takes no prefix', () => {
    process.env.QUESTDB_HTTP_URL = 'http://questdb:9000';
    expect(upstreamBase('questdb')).toBe('http://questdb:9000');
    delete process.env.QUESTDB_HTTP_URL;
    expect(upstreamBase('questdb')).toBe('http://127.0.0.1:9000');
  });

  it('deepquant is served at the root', () => {
    process.env.DEEP_QUANT_URL = 'http://deep-quant:8086';
    expect(upstreamBase('deepquant')).toBe('http://deep-quant:8086');
  });

  it('sentiment takes no prefix here — its route appends /sentiment itself', () => {
    process.env.SENTIMENT_HTTP_URL = 'http://sentiment:8090';
    expect(upstreamBase('sentiment')).toBe('http://sentiment:8090');
  });

  it('prefers the per-service override over the gateway base', () => {
    // Both set is the real droplet shape: the gateway base exists for other
    // consumers, but a service reachable on the internal network must not be
    // dialled through the public gateway (it would need a credential and add a hop).
    process.env.STRATAI_HTTP_BASE_URL = 'https://app-api.stratai.live';
    process.env.QUANT_TOOL_SERVER_URL = 'http://tool-server:8084';
    expect(upstreamBase('tools')).toBe('http://tool-server:8084/tools');
  });
});
