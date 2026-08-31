// @vitest-environment jsdom
//
// Why the ghost line stopped rendering once live ticks started flowing.
//
// `subscribeBars` watches the whole trade store, because that is where the live
// feed lands. But the feed rebroadcasts the in-progress candle on every tick of
// every subscribed instrument — 755 of them in production — and the store
// coalesces those into one write per animation frame. So this subscription ran
// ~60×/s and forwarded a bar to TradingView every time, including when THIS
// symbol's bar had not changed and some unrelated symbol was responsible for the
// write.
//
// `onTick` is an IPC call into the TradingView iframe. Sixty redundant ones a
// second saturate that channel and slow everything else crossing it — including
// `createMultipointShape`, which the ghost line needs ~20 awaited round-trips of
// per redraw. The ghost line's projection was fine; it was being starved of the
// bridge it draws over, so no draw ever completed.
//
// These tests pin the dedupe: identical bars are dropped, real changes are not.
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

vi.hoisted(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL ||= 'http://127.0.0.1:0/api/v1';
  process.env.NEXT_PUBLIC_DASHBOARD_URL ||= 'http://127.0.0.1:0/dashboard';
  process.env.NEXT_PUBLIC_AUTH_URL ||= 'https://auth.test.invalid';
});

const { kiteFetchSpy } = vi.hoisted(() => ({ kiteFetchSpy: vi.fn() }));
vi.mock('@/lib/kiteFetch', () => ({ kiteFetch: kiteFetchSpy }));

import { createDatafeed } from '@/charting/datafeed';
import { useTradeStore } from '@/store/useTradeStore';
import type { LibrarySymbolInfo, ResolutionString } from '@/charting/datafeedTypes';

const GUID = 'test-listener';
const BAR_MS = 1_772_000_000_000; // an arbitrary 10-minute bucket

function bar(overrides: Partial<{ close: number; high: number; low: number; volume: number }> = {}) {
  return {
    symbol: 'HDFCBANK',
    start_timestamp_ms: BAR_MS,
    open: 730,
    high: 731,
    low: 729,
    close: 730.5,
    volume: 1000,
    ...overrides,
  };
}

/** Subscribe the datafeed to HDFCBANK on 10m and return the onTick spy. */
function subscribe() {
  const onTick = vi.fn();
  createDatafeed().subscribeBars(
    { name: 'HDFCBANK' } as LibrarySymbolInfo,
    '10' as ResolutionString,
    onTick,
    GUID,
    () => {},
  );
  return onTick;
}

let datafeed: ReturnType<typeof createDatafeed>;

beforeEach(() => {
  datafeed = createDatafeed();
  useTradeStore.setState({ ohlcCandles: [], latencyMs: 0 });
});

afterEach(() => {
  datafeed.unsubscribeBars(GUID);
  vi.clearAllMocks();
});

describe('subscribeBars — redundant bars are not pushed into the widget', () => {
  it('forwards a bar the first time it appears', () => {
    const onTick = subscribe();

    useTradeStore.setState({ ohlcCandles: [bar()] });

    expect(onTick).toHaveBeenCalledTimes(1);
    expect(onTick).toHaveBeenCalledWith(
      expect.objectContaining({ time: BAR_MS, open: 730, close: 730.5 }),
    );
  });

  it('does NOT forward again when an unrelated store write leaves the bar unchanged', () => {
    const onTick = subscribe();
    useTradeStore.setState({ ohlcCandles: [bar()] });
    expect(onTick).toHaveBeenCalledTimes(1);

    // Exactly what another symbol's tick looks like from here: the store is
    // written, this subscription fires, but HDFCBANK's bar is byte-identical.
    // This is the 60-times-a-second path that starved the iframe bridge.
    for (let i = 1; i <= 50; i += 1) {
      useTradeStore.setState({ latencyMs: i });
    }

    expect(onTick).toHaveBeenCalledTimes(1);
  });

  it('forwards again as soon as the price actually moves', () => {
    const onTick = subscribe();
    useTradeStore.setState({ ohlcCandles: [bar()] });
    useTradeStore.setState({ ohlcCandles: [bar({ close: 730.9 })] });

    expect(onTick).toHaveBeenCalledTimes(2);
    expect(onTick).toHaveBeenLastCalledWith(expect.objectContaining({ close: 730.9 }));
  });

  it('treats a high/low/volume-only change as a real update', () => {
    // The close can stay put while the bar's extremes or traded volume move, and
    // TradingView needs those: dedupe on the close alone would freeze the wick
    // and the volume bar of the forming candle.
    const onTick = subscribe();
    useTradeStore.setState({ ohlcCandles: [bar()] });

    useTradeStore.setState({ ohlcCandles: [bar({ high: 732 })] });
    useTradeStore.setState({ ohlcCandles: [bar({ high: 732, low: 728 })] });
    useTradeStore.setState({ ohlcCandles: [bar({ high: 732, low: 728, volume: 2000 })] });

    expect(onTick).toHaveBeenCalledTimes(4);
  });

  it('ignores writes for other symbols entirely', () => {
    const onTick = subscribe();
    useTradeStore.setState({ ohlcCandles: [bar()] });
    expect(onTick).toHaveBeenCalledTimes(1);

    useTradeStore.setState({
      ohlcCandles: [bar(), { ...bar(), symbol: 'RELIANCE', close: 1276 }],
    });

    // RELIANCE moving is not a reason to tell the HDFCBANK chart anything.
    expect(onTick).toHaveBeenCalledTimes(1);
  });
});
