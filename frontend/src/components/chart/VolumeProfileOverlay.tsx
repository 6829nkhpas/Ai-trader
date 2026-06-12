'use client';

// Feature: professional-charting-suite
//
// VolumeProfileOverlay — canvas rendering adapter for the pure
// `VolumeProfileEngine`.
//
// All binning / POC / Value_Area math lives in
// `charting/engines/volumeProfileEngine.ts`. This component is a thin renderer:
// it slices the candle series for the selected profile range, asks the engine
// to build the profile, caches the result, and draws the rows, markers and
// developing value area to a supersampled canvas.
//
// Behaviours implemented here:
//  - Per-row volume bars aligned to the price scale (Req 7.6).
//  - Distinct value-area row styling (Req 7.7).
//  - POC / VAH / VAL markers, omitted on an empty profile (Req 7.9).
//  - Developing value area for the active session (Req 7.8).
//  - Empty-profile indication when total volume is zero (Req 7.9).
//  - Debounced (200 ms) recompute on visible-range pan/zoom (Req 7.5).
//  - Visible / session / fixed-range anchor handling (Req 7.1).

import React, { useEffect, useRef, useCallback } from 'react';
import type { ChartCandle, VolumeBar } from '../../utils/chartTypes';
import {
  buildProfile,
  backingStoreLength,
  DEFAULT_PROFILE_ROWS,
  DEFAULT_VALUE_AREA_PERCENT,
  type ProfileRange,
  type ProfileRangeSpec,
  type VolumeProfile,
} from '../../charting/engines';

/** Debounce window for visible-range recompute (Requirement 7.5). */
const VISIBLE_RECOMPUTE_DEBOUNCE_MS = 200;

interface VolumeProfileOverlayProps {
  chartRef: React.RefObject<any>;
  candleSeriesRef: React.RefObject<any>;
  chartData: ChartCandle[];
  volumeData: VolumeBar[];
  /** Profile range mode (Requirement 7.1). Defaults to `visible`. */
  range?: ProfileRange;
  /** Number of price-level rows (Requirement 7.2). Defaults to 24. */
  rows?: number;
  /** Value-area target percentage (Requirement 7.4). Defaults to 70. */
  valuePercent?: number;
  /**
   * Render the developing value area for the active session in addition to the
   * main profile (Requirement 7.8).
   */
  showDevelopingValueArea?: boolean;
  /**
   * Inclusive `[start, end]` time anchors for the `fixed` range
   * (Requirement 7.1 / 7.6). Required when `range === 'fixed'`.
   */
  fixedRange?: { start: number; end: number };
  /**
   * Explicit session start time. When omitted, the active session is derived as
   * the UTC-day span of the most recent candle.
   */
  sessionStartTime?: number;
  /**
   * Notified when a fixed range is accepted (`false`) or rejected as invalid
   * (`true`) so the host can present an invalid-anchor indication
   * (Requirement 7.10).
   */
  onInvalidFixedRange?: (invalid: boolean) => void;
}

export default function VolumeProfileOverlay({
  chartRef,
  candleSeriesRef,
  chartData,
  volumeData,
  range = 'visible',
  rows = DEFAULT_PROFILE_ROWS,
  valuePercent = DEFAULT_VALUE_AREA_PERCENT,
  showDevelopingValueArea = false,
  fixedRange,
  sessionStartTime,
  onInvalidFixedRange,
}: VolumeProfileOverlayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number>(0);
  const needsRedrawRef = useRef(true);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Latest props accessible to the rAF loop without stale closures.
  const propsRef = useRef({
    chartData,
    volumeData,
    range,
    rows,
    valuePercent,
    showDevelopingValueArea,
    fixedRange,
    sessionStartTime,
    onInvalidFixedRange,
  });
  propsRef.current = {
    chartData,
    volumeData,
    range,
    rows,
    valuePercent,
    showDevelopingValueArea,
    fixedRange,
    sessionStartTime,
    onInvalidFixedRange,
  };

  // Cached engine output. Retained across invalid fixed ranges (Req 7.10) and
  // re-projected to current price coordinates every frame.
  const profileRef = useRef<VolumeProfile | null>(null);
  const developingProfileRef = useRef<VolumeProfile | null>(null);

  // ── Derive the active-session candle slice (Req 7.8) ──────────────────────
  const sessionCandles = useCallback(
    (cData: ChartCandle[], explicitStart?: number): ChartCandle[] => {
      if (cData.length === 0) return [];
      let start = explicitStart;
      if (typeof start !== 'number' || !Number.isFinite(start)) {
        // Derive the UTC-day start of the most recent candle. `time` is a UNIX
        // timestamp in seconds (lightweight-charts) or milliseconds; detect the
        // unit by magnitude so the same logic works for both.
        const last = cData[cData.length - 1].time;
        const isMs = last > 1e12;
        const dayMs = 86_400_000;
        const ms = isMs ? last : last * 1000;
        const dayStartMs = Math.floor(ms / dayMs) * dayMs;
        start = isMs ? dayStartMs : Math.floor(dayStartMs / 1000);
      }
      return cData.filter((c) => c.time >= (start as number));
    },
    [],
  );

  // ── Recompute the cached profile(s) via the engine ───────────────────────
  const recompute = useCallback(() => {
    try {
      const chart = chartRef.current;
      const p = propsRef.current;
      const cData = p.chartData;
      const vData = p.volumeData;
      if (!chart || cData.length === 0) {
        profileRef.current = null;
        developingProfileRef.current = null;
        return;
      }

      const opts = { rows: p.rows, valuePercent: p.valuePercent };

      // ── Select candles + range spec for the requested mode (Req 7.1) ──────
      let ranged: ChartCandle[] = cData;
      let spec: ProfileRangeSpec = { kind: 'visible' };

      if (p.range === 'fixed') {
        const fr = p.fixedRange;
        const invalid =
          !fr ||
          !Number.isFinite(fr.start) ||
          !Number.isFinite(fr.end) ||
          fr.end <= fr.start;
        // Notify the host of the validity transition (Req 7.10).
        p.onInvalidFixedRange?.(invalid);
        if (invalid) {
          // Retain the previously computed profile unchanged (Req 7.10).
          return;
        }
        ranged = cData; // engine filters to the inclusive [start, end] span.
        spec = { kind: 'fixed', start: fr.start, end: fr.end };
      } else if (p.range === 'session') {
        ranged = sessionCandles(cData, p.sessionStartTime);
        spec = { kind: 'session' };
      } else {
        // Visible range: slice to the chart's current visible logical range.
        let logicalRange: any;
        try {
          logicalRange = chart.timeScale().getVisibleLogicalRange();
        } catch {
          logicalRange = null;
        }
        if (
          logicalRange &&
          logicalRange.from != null &&
          logicalRange.to != null &&
          !isNaN(logicalRange.from) &&
          !isNaN(logicalRange.to)
        ) {
          const fromIdx = Math.max(0, Math.floor(logicalRange.from));
          const toIdx = Math.min(cData.length - 1, Math.ceil(logicalRange.to));
          ranged = fromIdx <= toIdx ? cData.slice(fromIdx, toIdx + 1) : [];
        }
        spec = { kind: 'visible' };
      }

      profileRef.current = buildProfile(ranged, vData, {
        ...opts,
        range: spec,
        previousProfile: profileRef.current,
      });

      // ── Developing value area over the accumulated session (Req 7.8) ──────
      if (p.showDevelopingValueArea) {
        const sess = sessionCandles(cData, p.sessionStartTime);
        developingProfileRef.current = buildProfile(sess, vData, {
          ...opts,
          range: { kind: 'session' },
        });
      } else {
        developingProfileRef.current = null;
      }

      needsRedrawRef.current = true;
    } catch {
      // Ignore transient recompute errors; keep the last good profile.
    }
  }, [chartRef, sessionCandles]);

  // ── Core Drawing Function (pure projection of the cached profile) ─────────
  const draw = useCallback(() => {
    try {
      const canvas = canvasRef.current;
      const chart = chartRef.current;
      const series = candleSeriesRef.current;
      if (!canvas || !chart || !series) return;

      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      const parent = canvas.parentElement;
      if (!parent) return;

      const rect = parent.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return;

      // ── 4K Supersampling ─────────────────────────────────────────────────
      const dpr = (window.devicePixelRatio || 1) * 2;
      const bw = backingStoreLength(rect.width, dpr);
      const bh = backingStoreLength(rect.height, dpr);
      if (canvas.width !== bw || canvas.height !== bh) {
        canvas.width = bw;
        canvas.height = bh;
      }

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, rect.width, rect.height);

      const w = rect.width;
      const rightMargin = 65; // lightweight-charts right price scale
      const profileMaxW = w * 0.3;
      const barAnchorX = w - rightMargin;

      const profile = profileRef.current;
      if (!profile) return;

      // ── Empty-profile indication, no POC/VAH/VAL markers (Req 7.9) ────────
      if (profile.totalVolume <= 0 || profile.poc == null) {
        ctx.fillStyle = 'rgba(148, 163, 184, 0.55)';
        ctx.font = '11px monospace';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'top';
        ctx.fillText('No volume in range', barAnchorX, 8);
        return;
      }

      // Greatest row volume scales bar width.
      let maxVol = 0;
      for (const r of profile.rows) if (r.volume > maxVol) maxVol = r.volume;
      if (maxVol <= 0) return;

      // ── Developing value-area band (drawn behind the bars) (Req 7.8) ──────
      const developing = developingProfileRef.current;
      if (developing && developing.vah != null && developing.val != null) {
        let yVah: number | null = null;
        let yVal: number | null = null;
        try {
          yVah = series.priceToCoordinate(developing.vah);
          yVal = series.priceToCoordinate(developing.val);
        } catch {
          yVah = null;
        }
        if (yVah != null && yVal != null && !isNaN(yVah) && !isNaN(yVal)) {
          const top = Math.min(yVah, yVal);
          const h = Math.max(1, Math.abs(yVal - yVah));
          ctx.fillStyle = 'rgba(56, 189, 248, 0.07)'; // sky tint
          ctx.fillRect(0, top, barAnchorX, h);
        }
        if (developing.poc != null) {
          let yDevPoc: number | null = null;
          try {
            yDevPoc = series.priceToCoordinate(developing.poc);
          } catch {
            yDevPoc = null;
          }
          if (yDevPoc != null && !isNaN(yDevPoc)) {
            const yp = Math.round(yDevPoc) + 0.5;
            ctx.strokeStyle = 'rgba(56, 189, 248, 0.55)';
            ctx.lineWidth = 1;
            ctx.setLineDash([2, 3]);
            ctx.beginPath();
            ctx.moveTo(0, yp);
            ctx.lineTo(barAnchorX, yp);
            ctx.stroke();
            ctx.setLineDash([]);
          }
        }
      }

      // ── Per-row volume bars (Req 7.6) w/ distinct VA styling (Req 7.7) ────
      for (const row of profile.rows) {
        let yHi: number | null;
        let yLo: number | null;
        try {
          yHi = series.priceToCoordinate(row.priceHigh);
          yLo = series.priceToCoordinate(row.priceLow);
        } catch {
          continue;
        }
        if (yHi == null || yLo == null || isNaN(yHi) || isNaN(yLo)) continue;

        const barH = Math.max(1, Math.abs(yLo - yHi));
        const barW = (row.volume / maxVol) * profileMaxW;
        const yTop = Math.round(Math.min(yHi, yLo));
        const xStart = Math.round(barAnchorX - barW);
        const roundedBarW = Math.round(barW);
        const roundedBarH = Math.round(barH);

        // VA rows: warm amber. Non-VA: cool slate. (Req 7.7)
        ctx.fillStyle = row.inValueArea
          ? 'rgba(245, 158, 11, 0.35)'
          : 'rgba(148, 163, 184, 0.12)';
        ctx.fillRect(xStart, yTop, roundedBarW, roundedBarH);

        ctx.strokeStyle = row.inValueArea
          ? 'rgba(245, 158, 11, 0.18)'
          : 'rgba(148, 163, 184, 0.06)';
        ctx.lineWidth = 0.5;
        ctx.strokeRect(xStart + 0.5, yTop + 0.5, roundedBarW, roundedBarH);
      }

      // ── POC marker (pink, dashed, full width) (Req 7.3) ───────────────────
      let yPoc: number | null;
      try {
        yPoc = series.priceToCoordinate(profile.poc);
      } catch {
        yPoc = null;
      }
      if (yPoc != null && !isNaN(yPoc)) {
        const yP = Math.round(yPoc) + 0.5;
        ctx.strokeStyle = '#ec4899';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 3]);
        ctx.beginPath();
        ctx.moveTo(0, yP);
        ctx.lineTo(barAnchorX, yP);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = '#ec4899';
        ctx.font = 'bold 10px monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(`POC ${profile.poc.toFixed(2)}`, 8, yP - 10);
      }

      // ── VAH / VAL markers (purple, dashed) (Req 7.4) ──────────────────────
      const markers: { price: number | null; label: string }[] = [
        { price: profile.vah, label: 'VAH' },
        { price: profile.val, label: 'VAL' },
      ];
      for (const { price, label } of markers) {
        if (price == null) continue;
        let yLine: number | null;
        try {
          yLine = series.priceToCoordinate(price);
        } catch {
          continue;
        }
        if (yLine == null || isNaN(yLine)) continue;

        const yL = Math.round(yLine) + 0.5;
        ctx.strokeStyle = 'rgba(168, 85, 247, 0.5)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(0, yL);
        ctx.lineTo(barAnchorX, yL);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = 'rgba(168, 85, 247, 0.7)';
        ctx.font = '9px monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(`${label} ${price.toFixed(2)}`, 8, yL - 8);
      }
    } catch {
      // Silently swallow any transient drawing errors.
    }
  }, [chartRef, candleSeriesRef]);

  // ── requestAnimationFrame Render Loop ──────────────────────────────────────
  useEffect(() => {
    let active = true;
    const loop = () => {
      if (!active) return;
      if (needsRedrawRef.current) {
        needsRedrawRef.current = false;
        draw();
      }
      rafRef.current = requestAnimationFrame(loop);
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => {
      active = false;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [draw]);

  // ── Chart event subscriptions ──────────────────────────────────────────────
  // Visible-range changes are debounced (Req 7.5): each pan/zoom event schedules
  // a recompute 200 ms in the future, while still repainting immediately so the
  // cached profile re-projects smoothly to the new price coordinates.
  useEffect(() => {
    let active = true;
    let unsubFn: (() => void) | null = null;

    const onVisibleRangeChange = () => {
      needsRedrawRef.current = true; // reposition cached profile this frame
      if (propsRef.current.range !== 'visible') return; // only visible debounces
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
      debounceTimerRef.current = setTimeout(() => {
        recompute();
      }, VISIBLE_RECOMPUTE_DEBOUNCE_MS);
    };

    const onResize = () => {
      needsRedrawRef.current = true;
    };

    const trySubscribe = () => {
      const chart = chartRef.current;
      if (!chart) {
        if (active) setTimeout(trySubscribe, 100);
        return;
      }
      try {
        chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange);
        unsubFn = () => {
          try {
            chart
              .timeScale()
              .unsubscribeVisibleLogicalRangeChange(onVisibleRangeChange);
          } catch {}
        };
      } catch {}
    };

    trySubscribe();
    window.addEventListener('resize', onResize);

    return () => {
      active = false;
      unsubFn?.();
      window.removeEventListener('resize', onResize);
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
    };
  }, [chartRef, recompute]);

  // ── Recompute immediately on data / configuration changes ──────────────────
  // Data changes (incl. each new session interval) and config changes recompute
  // without debounce; developing value area thus tracks the session (Req 7.8).
  useEffect(() => {
    recompute();
    needsRedrawRef.current = true;
  }, [
    recompute,
    chartData,
    volumeData,
    range,
    rows,
    valuePercent,
    showDevelopingValueArea,
    fixedRange?.start,
    fixedRange?.end,
    sessionStartTime,
  ]);

  // ── Inline styles guarantee pixel-perfect sizing ───────────────────────────
  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        zIndex: 5,
      }}
    />
  );
}
