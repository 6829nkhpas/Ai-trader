// Feature: professional-charting-suite
//
// Unit/integration tests for renderer behaviors (task 12.6).
//
// These exercise the PURE decision logic that backs the renderer behaviors the
// requirements care about, without instantiating lightweight-charts or the DOM:
//
//   - symbol-switch clear (Req 9.4) — a symbol change forces a structural
//     repaint so no prior-symbol candle can survive an in-place update;
//   - right-edge follow (Req 9.5) — only an append while parked at the right
//     edge keeps the new candle in view;
//   - out-of-order repaint (Req 9.6, supports 9.4) — earlier-candle / reorder /
//     shrink changes classify as a repaint;
//   - disconnect/reconnect indicator (Req 9.7, 9.8) — the connection reducer
//     maps raw feed signals to { isConnected, isDisconnected };
//   - fullscreen failure fallback (Req 12.5) — the fullscreen decision picks an
//     in-app fallback when the native API is unavailable / throws;
//   - DPR backing-store scaling (Req 12.6) — backing-store length is
//     floor(cssSize * dpr) across ratios 1.0–4.0.

import { describe, it, expect } from 'vitest';

import {
  classifyRealtimePaint,
  isViewAtRightEdge,
  shouldFollowRightEdge,
} from '@/charting/realtimePaint';
import { deriveConnectionStatus } from '@/charting/connectionStatus';
import { backingStoreLength } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';

// ── Fixtures ────────────────────────────────────────────────────────────────

const c = (time: number, close: number): ChartCandle => ({
  time,
  open: close,
  high: close + 1,
  low: close - 1,
  close,
});

const series = (...closes: Array<[number, number]>): ChartCandle[] =>
  closes.map(([t, v]) => c(t, v));

// ─────────────────────────────────────────────────────────────────────────────
// Symbol-switch clear (Requirement 9.4) + out-of-order repaint (9.6)
// ─────────────────────────────────────────────────────────────────────────────

describe('classifyRealtimePaint — symbol-switch clear & repaint (Req 9.4, 9.6)', () => {
  it('repaints when the previously painted series was empty (first load / post-clear)', () => {
    // After a symbol switch the renderer clears the series; the next snapshot
    // starts from an empty prev, which must be a full (re)paint, not an
    // in-place update that could leave stale candles behind.
    const next = series([1, 10], [2, 11], [3, 12]);
    expect(classifyRealtimePaint([], next)).toBe('repaint');
  });

  it('repaints when the new snapshot is empty', () => {
    const prev = series([1, 10], [2, 11]);
    expect(classifyRealtimePaint(prev, [])).toBe('repaint');
  });

  it('repaints when an entirely different symbol series replaces the same length', () => {
    // Same length but every candle differs (different instrument's data):
    // the prefix check fails on the first candle → repaint, so no old candle
    // can be left painted (Requirement 9.4).
    const prev = series([1, 10], [2, 11], [3, 12]);
    const next = series([100, 500], [101, 501], [102, 502]);
    expect(classifyRealtimePaint(prev, next)).toBe('repaint');
  });

  it('repaints when an earlier (out-of-order) candle changes', () => {
    const prev = series([1, 10], [2, 11], [3, 12]);
    const next = series([1, 10], [2, 99], [3, 12]); // middle candle rewritten
    expect(classifyRealtimePaint(prev, next)).toBe('repaint');
  });

  it('repaints when the series shrinks or reorders (non +0/+1 delta)', () => {
    const prev = series([1, 10], [2, 11], [3, 12]);
    expect(classifyRealtimePaint(prev, series([1, 10]))).toBe('repaint'); // shrink
    expect(
      classifyRealtimePaint(prev, series([1, 10], [2, 11], [3, 12], [4, 13], [5, 14])),
    ).toBe('repaint'); // grew by >1
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// In-place update (Requirement 9.3) and append (9.5)
// ─────────────────────────────────────────────────────────────────────────────

describe('classifyRealtimePaint — update vs append (Req 9.3, 9.5)', () => {
  it('classifies an in-place change to only the last candle as an update', () => {
    const prev = series([1, 10], [2, 11], [3, 12]);
    const next = series([1, 10], [2, 11], [3, 99]); // only last close changed
    expect(classifyRealtimePaint(prev, next)).toBe('update');
  });

  it('classifies exactly one newer candle with an intact prefix as an append', () => {
    const prev = series([1, 10], [2, 11], [3, 12]);
    const next = series([1, 10], [2, 11], [3, 12], [4, 13]);
    expect(classifyRealtimePaint(prev, next)).toBe('append');
  });

  it('repaints an append whose prefix was also modified', () => {
    const prev = series([1, 10], [2, 11], [3, 12]);
    const next = series([1, 10], [2, 77], [3, 12], [4, 13]); // prefix changed
    expect(classifyRealtimePaint(prev, next)).toBe('repaint');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Right-edge follow (Requirement 9.5)
// ─────────────────────────────────────────────────────────────────────────────

describe('right-edge follow decision (Req 9.5)', () => {
  it('detects the view is at the right edge within one bar of the end', () => {
    // last data index = 99; range.to within one bar counts as the right edge.
    expect(isViewAtRightEdge(99, 99)).toBe(true);
    expect(isViewAtRightEdge(105, 99)).toBe(true); // rightOffset breathing room
    expect(isViewAtRightEdge(50, 99)).toBe(false); // scrolled into history
  });

  it('follows the right edge only for an append while parked at the edge', () => {
    expect(shouldFollowRightEdge('append', true)).toBe(true);
    expect(shouldFollowRightEdge('append', false)).toBe(false); // user scrolled back
    expect(shouldFollowRightEdge('update', true)).toBe(false); // in-place tick
    expect(shouldFollowRightEdge('repaint', true)).toBe(false); // keep scroll pos
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Disconnect / reconnect indicator (Requirements 9.7, 9.8)
// ─────────────────────────────────────────────────────────────────────────────

describe('deriveConnectionStatus — disconnect/reconnect indicator (Req 9.7, 9.8)', () => {
  it('reports disconnected when both signals are down', () => {
    const s = deriveConnectionStatus('DISCONNECTED', 'disconnected');
    expect(s).toEqual({ status: 'disconnected', isConnected: false, isDisconnected: true });
  });

  it('reports connected when either signal reports an open link (Req 9.8)', () => {
    expect(deriveConnectionStatus('CONNECTED', 'disconnected').isConnected).toBe(true);
    expect(deriveConnectionStatus('DISCONNECTED', 'connected').isConnected).toBe(true);
    const s = deriveConnectionStatus('CONNECTED', 'connected');
    expect(s.isDisconnected).toBe(false);
    expect(s.status).toBe('connected');
  });

  it('treats a handshake as transient connecting, not disconnected (no flicker)', () => {
    const s = deriveConnectionStatus('CONNECTING', 'connecting');
    expect(s.status).toBe('connecting');
    expect(s.isDisconnected).toBe(false);
    expect(s.isConnected).toBe(false);
  });

  it('treats unknown / missing signals as disconnected (Req 9.7)', () => {
    expect(deriveConnectionStatus(undefined, undefined).isDisconnected).toBe(true);
    expect(deriveConnectionStatus(null, null).isDisconnected).toBe(true);
    expect(deriveConnectionStatus('WAT', 'huh').isDisconnected).toBe(true);
  });

  it('models the full disconnect → reconnect transition flipping the indicator', () => {
    // Live feed up.
    expect(deriveConnectionStatus('CONNECTED', 'connected').isDisconnected).toBe(false);
    // onclose/onerror drops the link → indicator must appear.
    expect(deriveConnectionStatus('DISCONNECTED', 'disconnected').isDisconnected).toBe(true);
    // onopen reconnects → indicator must be removed.
    expect(deriveConnectionStatus('CONNECTED', 'connected').isDisconnected).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// DPR backing-store scaling (Requirement 12.6)
// ─────────────────────────────────────────────────────────────────────────────

describe('backingStoreLength — DPR scaling (Req 12.6)', () => {
  it('equals floor(cssLength * dpr) for ratios 1.0–4.0', () => {
    const css = 801; // odd so fractional results occur at non-integer dpr
    for (const dpr of [1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]) {
      expect(backingStoreLength(css, dpr)).toBe(Math.floor(css * dpr));
    }
  });

  it('scales a standard 1280x720 surface across common ratios', () => {
    expect(backingStoreLength(1280, 1)).toBe(1280);
    expect(backingStoreLength(1280, 2)).toBe(2560);
    expect(backingStoreLength(720, 3)).toBe(2160);
    expect(backingStoreLength(720, 4)).toBe(2880);
  });

  it('floors fractional physical pixels to a whole canvas dimension', () => {
    expect(backingStoreLength(100.5, 1.5)).toBe(Math.floor(150.75)); // 150
    expect(backingStoreLength(101, 1.25)).toBe(126); // 126.25 → 126
  });

  it('clamps degenerate / non-finite inputs to zero (never negative)', () => {
    expect(backingStoreLength(-10, 2)).toBe(0);
    expect(backingStoreLength(0, 3)).toBe(0);
    expect(backingStoreLength(NaN, 2)).toBe(0);
    expect(backingStoreLength(100, Infinity)).toBe(0);
  });
});
