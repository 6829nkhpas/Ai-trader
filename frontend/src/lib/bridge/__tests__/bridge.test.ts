// Behavioural tests for the browser transport.
//
// `environment: 'node'` in vitest.config.ts means `window` is undefined, so
// `isTauri()` is false and every call below exercises the browser path — which is
// exactly the path that was crashing.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BridgeUnsupportedError, bridgeInvoke, isCommandAvailable } from '../index';
import { __resetBridgeBus, bridgeListen, emitBridgeEvent, relaySse } from '../events';
import { cleanRadarSymbols, rowsToSearchResults } from '../webAdapters';

/** Build a `ReadableStream` of UTF-8 chunks, as `fetch().body` would yield. */
function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c));
      controller.close();
    },
  });
}

describe('bridgeInvoke off-Tauri', () => {
  it('throws BridgeUnsupportedError, never a TypeError', async () => {
    // This is the regression: previously `invoke` dereferenced
    // `window.__TAURI_INTERNALS__.invoke` and threw
    // "Cannot read properties of undefined (reading 'invoke')". Any command with
    // no adapter must fail as a typed, readable error instead.
    const err: unknown = await bridgeInvoke('check_for_update').catch((e) => e);
    expect(err).toBeInstanceOf(BridgeUnsupportedError);
    expect(err).not.toBeInstanceOf(TypeError);
    const unsupported = err as BridgeUnsupportedError;
    expect(unsupported.message).not.toContain('undefined');
    expect(unsupported.reason).toBe('desktop-only');
  });

  it('distinguishes desktop-only from native-path from callerless from typo', async () => {
    const cases: Array<[string, string]> = [
      ['check_for_update', 'desktop-only'],
      ['compute_ghost_curve', 'native-browser-path'],
      ['deploy_ai_sentinel', 'no-frontend-caller'],
      ['totally_made_up_command', 'unknown-command'],
    ];
    for (const [cmd, reason] of cases) {
      const err: unknown = await bridgeInvoke(cmd).catch((e) => e);
      expect(err, cmd).toBeInstanceOf(BridgeUnsupportedError);
      const unsupported = err as BridgeUnsupportedError;
      expect(unsupported.reason, cmd).toBe(reason);
      expect(unsupported.message.length, cmd).toBeGreaterThan(20);
    }
  });

  it('reports availability without throwing', () => {
    expect(isCommandAvailable('fetch_symbol_sentiment')).toBe(true);
    // The radar/pattern scans call quant-core via tool-server.
    expect(isCommandAvailable('scan_radar_symbol')).toBe(true);
    expect(isCommandAvailable('fno_list_chains')).toBe(true);
    // Still genuinely absent: no caller, so no adapter.
    expect(isCommandAvailable('deploy_ai_sentinel')).toBe(false);
  });
});

describe('adapters that talk HTTP', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function jsonRes(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  it('routes sentiment at the same origin', async () => {
    fetchMock.mockResolvedValue(jsonRes({ symbol: 'TCS', score: 42 }));
    const out = await bridgeInvoke('fetch_symbol_sentiment', { symbol: 'TCS' });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/sentiment?symbol=TCS');
    expect(out).toEqual({ symbol: 'TCS', score: 42 });
  });

  it('surfaces the upstream error message verbatim', async () => {
    // Rust commands reject with a plain String that the UI renders directly, so
    // the browser path must not replace it with a generic HTTP message.
    fetchMock.mockResolvedValue(jsonRes({ error: 'No sentiment computed yet for TCS.' }, 503));
    await expect(bridgeInvoke('fetch_symbol_sentiment', { symbol: 'TCS' })).rejects.toThrow(
      'No sentiment computed yet for TCS.',
    );
  });

  it('returns questdb results as text, matching fetch_questdb -> Result<String>', async () => {
    fetchMock.mockResolvedValue(new Response('{"dataset":[[1]]}'));
    const out = await bridgeInvoke('fetch_questdb', { query: 'select 1' });
    expect(typeof out).toBe('string');
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/questdb/exec?query=select%201');
  });

  it('reports pool status as a boolean and never throws', async () => {
    fetchMock.mockRejectedValue(new Error('network down'));
    await expect(bridgeInvoke('get_pool_status')).resolves.toBe(false);
    fetchMock.mockResolvedValue(new Response('ok'));
    await expect(bridgeInvoke('get_pool_status')).resolves.toBe(true);
  });

  it('queries every exchange for instrument search and survives one failing', async () => {
    fetchMock.mockImplementation((url: string) =>
      url.includes('NFO')
        ? Promise.reject(new Error('NFO cache cold'))
        : url.includes('BSE') || url.includes('BFO')
          ? Promise.resolve(jsonRes({ results: [] }))
          : Promise.resolve(
              jsonRes({
                results: [
                  { tradingsymbol: 'TCS', name: 'TCS', exchange: 'NSE', instrument_type: 'EQ' },
                ],
              }),
            ),
    );
    const out = (await bridgeInvoke('search_instruments', { query: 'TCS' })) as unknown[];
    // All four segments India actually has. BSE was the first missing leg — no BSE
    // index (SENSEX, BANKEX) could be found without it — and BFO is its derivative
    // half, without which no SENSEX option could be found either.
    expect(fetchMock).toHaveBeenCalledTimes(4);
    const exchangesQueried = fetchMock.mock.calls.map(
      (c: unknown[]) => String(c[0]).match(/exchange=(\w+)/)?.[1],
    );
    expect(exchangesQueried).toEqual(expect.arrayContaining(['NSE', 'BSE', 'NFO', 'BFO']));
    expect(out).toEqual([
      { kind: 'EQ', symbol: 'TCS', name: 'TCS', exchange: 'NSE', segment: undefined },
    ]);
  });

  it('finds SENSEX, which only exists on BSE', async () => {
    // SENSEX is a BSE index (segment INDICES, token 265). NSE's master carries
    // only the ETFs that track it, so an NSE-only search returned SENSEXETF and
    // friends and never the index — the reported "SENSEX is not in search".
    fetchMock.mockImplementation((url: string) =>
      url.includes('exchange=BSE')
        ? Promise.resolve(
            jsonRes({
              results: [
                {
                  tradingsymbol: 'SENSEX',
                  name: 'SENSEX',
                  exchange: 'BSE',
                  instrument_type: 'EQ',
                  segment: 'INDICES',
                },
              ],
            }),
          )
        : Promise.resolve(jsonRes({ results: [] })),
    );

    const out = (await bridgeInvoke('search_instruments', { query: 'SENSEX' })) as Array<
      Record<string, unknown>
    >;

    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ kind: 'EQ', symbol: 'SENSEX', segment: 'INDICES' });
  });

  it('finds a SENSEX option, which only exists on BFO', async () => {
    // The derivative half of the same exchange split. SENSEX contracts are listed
    // in the BFO master (`SENSEX2690376900CE`, segment BFO-OPT, lot 20); an
    // NFO-only search returns nothing for them.
    fetchMock.mockImplementation((url: string) =>
      url.includes('exchange=BFO')
        ? Promise.resolve(
            jsonRes({
              results: [
                {
                  tradingsymbol: 'SENSEX2690376900CE',
                  name: 'SENSEX',
                  exchange: 'BFO',
                  instrument_type: 'CE',
                  segment: 'BFO-OPT',
                  expiry: '2026-09-03',
                  strike: 76900,
                  lot_size: 20,
                },
              ],
            }),
          )
        : Promise.resolve(jsonRes({ results: [] })),
    );

    const out = (await bridgeInvoke('search_instruments', { query: 'SENSEX 76900 CE' })) as Array<
      Record<string, unknown>
    >;

    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ kind: 'FNO', tradingsymbol: 'SENSEX2690376900CE' });
  });

  it('short-circuits an empty search without a request', async () => {
    await expect(bridgeInvoke('search_instruments', { query: '   ' })).resolves.toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('omits an empty expiry so the backend picks the nearest', async () => {
    // `useTradeStore.fnoExpiry` defaults to '' meaning "nearest"; sending
    // `expiry=` would ask the service to match a literal empty expiry.
    fetchMock.mockResolvedValue(jsonRes({ unavailable: true }));
    await bridgeInvoke('get_fno_analytics', { underlying: 'NIFTY', expiry: '' });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/deepquant/options/snapshot?underlying=NIFTY');
  });

  it('rejects a missing required argument with a readable message', async () => {
    await expect(bridgeInvoke('get_fno_analytics', {})).rejects.toThrow(
      'get_fno_analytics: argument "underlying" must be a non-empty string',
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('local-store adapters', () => {
  it('mirrors RadarRegistry::set_symbols normalisation', () => {
    expect(cleanRadarSymbols([' tcs ', 'TCS', 'infy', '', '  ', 'RELIANCE'])).toEqual([
      'TCS',
      'INFY',
      'RELIANCE',
    ]);
    expect(cleanRadarSymbols('not an array')).toEqual([]);
  });

  it('returns "{}" for a missing workspace, matching db::load_workspace', async () => {
    // The Rust command maps QueryReturnedNoRows to Ok("{}"), and
    // `charting/workspace.ts` JSON.parses the result unconditionally.
    await expect(bridgeInvoke('load_workspace', { symbol: 'NOSUCH' })).resolves.toBe('{}');
  });

  it('reports a failed write instead of claiming a save that did not happen', async () => {
    // Node has no localStorage. The adapter must surface that rather than
    // resolving, because localStorage IS the registry/workspace store on the
    // web — a silent success would make `charting/workspace.ts` report a durable
    // save and `useRadarStore.syncRegistry` believe the symbol set was pushed.
    // Callers that can tolerate it (`syncRegistry`, `persist`) catch and warn.
    await expect(bridgeInvoke('set_radar_symbols', { symbols: ['tcs'] })).rejects.toThrow(
      /localStorage is unavailable/,
    );
    await expect(bridgeInvoke('save_workspace', { symbol: 'TCS', stateJson: '{}' })).rejects.toThrow(
      /localStorage is unavailable/,
    );
    // Reads stay tolerant: a missing store is indistinguishable from a missing
    // key, and both mean "no saved state", which is not an error.
    await expect(bridgeInvoke('get_radar_symbols')).resolves.toEqual([]);
  });
});

describe('search result mapping', () => {
  it('produces the SearchResult tagged union datafeed.ts expects', () => {
    const out = rowsToSearchResults([
      { tradingsymbol: 'NIFTY 50', name: 'NIFTY 50', exchange: 'NSE', instrument_type: 'INDEX' },
      {
        tradingsymbol: 'NIFTY25AUG24000CE',
        name: 'NIFTY',
        exchange: 'NFO',
        instrument_type: 'CE',
        expiry: '2026-08-27',
        strike: 24000,
      },
      {
        tradingsymbol: 'NIFTY25AUGFUT',
        name: 'NIFTY',
        exchange: 'NFO',
        instrument_type: 'FUT',
        expiry: '2026-08-27',
        strike: 0,
      },
    ]);
    expect(out[0]).toMatchObject({
      kind: 'EQ',
      symbol: 'NIFTY 50',
      name: 'NIFTY 50',
      exchange: 'NSE',
    });
    expect(out[1]).toMatchObject({ kind: 'FNO', optionType: 'CE', strike: 24000 });
    // A future has no strike; `SearchResult::Fno.strike` is None there, and
    // datafeed.ts branches on optionType === 'FUT' for the description.
    expect(out[2]).toMatchObject({ kind: 'FNO', optionType: 'FUT', strike: null });
  });

  it('carries the segment through so an index can be recognised as one', () => {
    // Kite reports index rows as segment INDICES with instrument_type EQ, so the
    // segment is the only field that distinguishes `NIFTY BANK` from `NIFTYETF`.
    // Dropping it here forced every consumer back onto a hand-written name list.
    const out = rowsToSearchResults([
      {
        tradingsymbol: 'NIFTY BANK',
        name: 'NIFTY BANK',
        exchange: 'NSE',
        instrument_type: 'EQ',
        segment: 'INDICES',
      },
      {
        tradingsymbol: 'NIFTYETF',
        name: 'Nifty ETF',
        exchange: 'NSE',
        instrument_type: 'EQ',
        segment: 'NSE',
      },
    ]);

    expect(out[0]).toMatchObject({ kind: 'EQ', symbol: 'NIFTY BANK', segment: 'INDICES' });
    expect(out[1]).toMatchObject({ kind: 'EQ', symbol: 'NIFTYETF', segment: 'NSE' });
  });
});

describe('event bus', () => {
  beforeEach(() => __resetBridgeBus());

  it('delivers the Tauri event envelope shape', async () => {
    const seen: unknown[] = [];
    await bridgeListen('quant-consensus', (e) => seen.push(e));
    emitBridgeEvent('quant-consensus', { symbol: 'TCS', trend_score: 12 });
    expect(seen).toEqual([
      { event: 'quant-consensus', payload: { symbol: 'TCS', trend_score: 12 }, id: 0 },
    ]);
  });

  it('stops delivering after unlisten', async () => {
    const seen: unknown[] = [];
    const unlisten = await bridgeListen('radar-alert', (e) => seen.push(e.payload));
    emitBridgeEvent('radar-alert', 1);
    unlisten();
    emitBridgeEvent('radar-alert', 2);
    expect(seen).toEqual([1]);
  });

  it('survives a listener that throws and a listener that unsubscribes mid-dispatch', async () => {
    const seen: number[] = [];
    let off: (() => void) | undefined;
    await bridgeListen('ohlc-tick', () => {
      throw new Error('boom');
    });
    off = await bridgeListen('ohlc-tick', () => off?.());
    await bridgeListen('ohlc-tick', () => seen.push(1));
    expect(() => emitBridgeEvent('ohlc-tick', {})).not.toThrow();
    expect(seen).toEqual([1]);
  });
});

describe('SSE relay', () => {
  it('parses frames the way relay_deep_quant_sse does', async () => {
    const frames: Array<{ event: string; data: unknown }> = [];
    await relaySse(
      streamOf(
        'event: RUN_STARTED\ndata: {"thread_id":"t1"}\n\n',
        'event: TOKEN\ndata: {"a":1}\n', // split mid-frame across chunks
        'data: \n\nevent: RUN_FINISHED\r\ndata: {"status":"paused"}\r\n\r\n',
      ),
      (f) => frames.push(f),
    );
    expect(frames.map((f) => f.event)).toEqual(['RUN_STARTED', 'TOKEN', 'RUN_FINISHED']);
    expect(frames[0].data).toEqual({ thread_id: 't1' });
    expect(frames[2].data).toEqual({ status: 'paused' });
  });

  it('yields null for unparseable data instead of dropping the frame', async () => {
    // Rust: `serde_json::from_str(...).unwrap_or(Value::Null)`.
    const frames: Array<{ event: string; data: unknown }> = [];
    await relaySse(streamOf('event: ERROR\ndata: not json\n\n'), (f) => frames.push(f));
    expect(frames).toEqual([{ event: 'ERROR', data: null }]);
  });

  it('ignores a data block with no event line', async () => {
    // SSE comments and keep-alives arrive as `: ping` with no event type.
    const frames: unknown[] = [];
    await relaySse(streamOf(': ping\n\ndata: {"x":1}\n\n'), (f) => frames.push(f));
    expect(frames).toEqual([]);
  });

  it('stops when the signal aborts', async () => {
    const controller = new AbortController();
    const frames: unknown[] = [];
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(encoder.encode('event: TOKEN\ndata: {"n":1}\n\n'));
        // Never closed: only the abort can end the relay.
      },
    });
    const done = relaySse(
      body,
      (f) => {
        frames.push(f);
        controller.abort();
      },
      controller.signal,
    );
    await expect(done).resolves.toBeUndefined();
    expect(frames).toHaveLength(1);
  });
});
