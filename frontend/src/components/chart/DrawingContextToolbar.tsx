'use client';

// Feature: professional-charting-suite
//
// DrawingContextToolbar — the floating per-drawing toolbar (TradingView-style)
// that appears anchored above the currently selected drawing. It exposes the
// per-drawing actions that the store now supports: recolor, stroke width, lock,
// clone, hide, delete, and z-order ("visual order") controls.
//
// Positioning mirrors `DrawingOverlays`: it converts the selected drawing's
// anchor points to pixel coordinates and re-renders on every pan/zoom so it
// tracks the drawing. It renders nothing when no drawing is selected.

import React, { useEffect, useRef, useState } from 'react';
import type { IChartApi, ISeriesApi } from 'lightweight-charts';
import {
  Palette,
  Minus,
  Lock,
  Unlock,
  Copy,
  EyeOff,
  Trash2,
  MoreHorizontal,
  ChevronsUp,
  ChevronsDown,
  ArrowUp,
  ArrowDown,
} from 'lucide-react';
import { useChartUIStore } from '../../store/useChartUIStore';
import { useOutsideClose } from '../../hooks/useOutsideClose';

interface Props {
  chartRef: React.RefObject<IChartApi | null>;
  candleSeriesRef: React.RefObject<ISeriesApi<'Candlestick'> | null>;
}

const APPROX_WIDTH = 250;
const WIDTHS = [1, 2, 3, 4];

export default function DrawingContextToolbar({ chartRef, candleSeriesRef }: Props) {
  const selectedId = useChartUIStore((s) => s.selectedDrawingId);
  const drawing = useChartUIStore((s) =>
    s.drawings.find((d) => d.id === s.selectedDrawingId),
  );

  const updateDrawing = useChartUIStore((s) => s.updateDrawing);
  const setDrawingLineWidth = useChartUIStore((s) => s.setDrawingLineWidth);
  const toggleDrawingLock = useChartUIStore((s) => s.toggleDrawingLock);
  const toggleDrawingHidden = useChartUIStore((s) => s.toggleDrawingHidden);
  const duplicateDrawing = useChartUIStore((s) => s.duplicateDrawing);
  const removeDrawing = useChartUIStore((s) => s.removeDrawing);
  const bringToFront = useChartUIStore((s) => s.bringDrawingToFront);
  const sendToBack = useChartUIStore((s) => s.sendDrawingToBack);
  const bringForward = useChartUIStore((s) => s.bringDrawingForward);
  const sendBackward = useChartUIStore((s) => s.sendDrawingBackward);

  const outerRef = useRef<HTMLDivElement>(null);
  const colorInputId = useRef(`dctb-color-${Math.random().toString(36).slice(2)}`).current;

  const [, setTick] = useState(0);
  const [moreOpen, setMoreOpen] = useState(false);
  const [widthOpen, setWidthOpen] = useState(false);
  const moreRef = useOutsideClose<HTMLDivElement>(() => setMoreOpen(false));
  const widthRef = useOutsideClose<HTMLDivElement>(() => setWidthOpen(false));

  // Re-render on pan/zoom so the toolbar tracks the drawing.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const force = () => setTick((t) => t + 1);
    chart.timeScale().subscribeVisibleTimeRangeChange(force);
    chart.timeScale().subscribeVisibleLogicalRangeChange(force);
    chart.timeScale().subscribeSizeChange(force);
    return () => {
      chart.timeScale().unsubscribeVisibleTimeRangeChange(force);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(force);
      chart.timeScale().unsubscribeSizeChange(force);
    };
  }, [chartRef]);

  if (!selectedId || !drawing || drawing.hidden) return null;

  const chart = chartRef.current;
  const series = candleSeriesRef.current;
  if (!chart || !series || drawing.points.length === 0) return null;

  // Compute the drawing's pixel bounding box from its anchor points.
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  for (const pt of drawing.points) {
    let x: number | null = null;
    try {
      x = chart.timeScale().timeToCoordinate(pt.time as never);
    } catch {
      x = null;
    }
    const y = series.priceToCoordinate(pt.price);
    if (x === null || y === null) continue;
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;

  const containerW = outerRef.current?.clientWidth ?? 800;
  const centerX = (minX + maxX) / 2;
  const left = Math.max(4, Math.min(centerX - APPROX_WIDTH / 2, containerW - APPROX_WIDTH - 4));
  const top = Math.max(4, minY - 46);

  const width = drawing.lineWidth ?? 2;

  const iconBtn =
    'flex h-7 w-7 items-center justify-center rounded text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary';

  return (
    <div ref={outerRef} className="pointer-events-none absolute inset-0 z-30 overflow-hidden">
      <div
        className="pointer-events-auto absolute flex items-center gap-0.5 rounded-lg border border-border-default bg-surface/95 px-1 py-1 shadow-2xl backdrop-blur-xl"
        style={{ left, top }}
      >
        {/* Color */}
        <label htmlFor={colorInputId} className={`${iconBtn} cursor-pointer relative`} title="Color">
          <Palette size={15} />
          <span
            className="pointer-events-none absolute bottom-1 right-1 h-1.5 w-1.5 rounded-full border border-black/30"
            style={{ backgroundColor: drawing.color ?? '#2962FF' }}
          />
          <input
            id={colorInputId}
            type="color"
            value={drawing.color ?? '#2962FF'}
            onChange={(e) => updateDrawing(drawing.id, { color: e.target.value })}
            className="absolute h-0 w-0 opacity-0"
          />
        </label>

        {/* Width */}
        <div className="relative" ref={widthRef}>
          <button type="button" className={`${iconBtn} w-auto gap-1 px-1.5`} title="Line width" onClick={() => setWidthOpen((v) => !v)}>
            <Minus size={15} />
            <span className="text-[10px] font-semibold tabular-nums">{width}px</span>
          </button>
          {widthOpen && (
            <div className="absolute left-0 top-full z-50 mt-1 w-24 rounded-lg border border-border-default bg-surface/95 p-1 shadow-2xl backdrop-blur-xl">
              {WIDTHS.map((w) => (
                <button
                  key={w}
                  type="button"
                  onClick={() => {
                    setDrawingLineWidth(drawing.id, w);
                    setWidthOpen(false);
                  }}
                  className={`flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[11px] transition-colors ${w === width ? 'bg-primary/10 text-primary' : 'text-text-secondary hover:bg-elevated hover:text-text-primary'}`}
                >
                  <span className="inline-block w-8 rounded-full bg-current" style={{ height: w }} />
                  <span className="tabular-nums">{w}px</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="mx-0.5 h-5 w-px bg-border-default/60" />

        {/* Lock */}
        <button
          type="button"
          className={`${iconBtn} ${drawing.locked ? 'text-primary' : ''}`}
          title={drawing.locked ? 'Unlock' : 'Lock'}
          onClick={() => toggleDrawingLock(drawing.id)}
        >
          {drawing.locked ? <Lock size={15} /> : <Unlock size={15} />}
        </button>

        {/* Clone */}
        <button type="button" className={iconBtn} title="Clone" onClick={() => duplicateDrawing(drawing.id)}>
          <Copy size={15} />
        </button>

        {/* Hide */}
        <button type="button" className={iconBtn} title="Hide" onClick={() => toggleDrawingHidden(drawing.id)}>
          <EyeOff size={15} />
        </button>

        {/* Delete */}
        <button
          type="button"
          className={`${iconBtn} hover:text-red-400`}
          title="Delete"
          onClick={() => removeDrawing(drawing.id)}
        >
          <Trash2 size={15} />
        </button>

        {/* More → visual order */}
        <div className="relative" ref={moreRef}>
          <button type="button" className={iconBtn} title="More" onClick={() => setMoreOpen((v) => !v)}>
            <MoreHorizontal size={15} />
          </button>
          {moreOpen && (
            <div className="absolute right-0 top-full z-50 mt-1 w-44 rounded-lg border border-border-default bg-surface/95 p-1 shadow-2xl backdrop-blur-xl">
              <div className="px-2 py-1 text-[9px] font-semibold uppercase tracking-wider text-text-muted/70">
                Visual order
              </div>
              {[
                { label: 'Bring to front', icon: ChevronsUp, fn: bringToFront },
                { label: 'Bring forward', icon: ArrowUp, fn: bringForward },
                { label: 'Send backward', icon: ArrowDown, fn: sendBackward },
                { label: 'Send to back', icon: ChevronsDown, fn: sendToBack },
              ].map(({ label, icon: Icon, fn }) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => {
                    fn(drawing.id);
                    setMoreOpen(false);
                  }}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[11px] text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
                >
                  <Icon size={13} className="text-text-muted" />
                  <span>{label}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
