'use client';

// Feature: professional-charting-suite
//
// DrawingLayersPanel — a layer-manager panel for chart drawings. It lists every
// drawing (front-most at the top, matching paint order), lets the user select,
// reorder (bring forward / send backward), toggle visibility, lock, and delete
// each one individually. Opened from the Layers button in the drawing toolbar.

import React from 'react';
import {
  X,
  Eye,
  EyeOff,
  Lock,
  Unlock,
  Trash2,
  ArrowUp,
  ArrowDown,
  Layers as LayersIcon,
} from 'lucide-react';
import { useChartUIStore } from '../../store/useChartUIStore';

/** Prettify a tool id like `fib-retracement` → `Fib Retracement`. */
function toolLabel(tool: string): string {
  return tool
    .split('-')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

export default function DrawingLayersPanel() {
  const drawings = useChartUIStore((s) => s.drawings);
  const selectedId = useChartUIStore((s) => s.selectedDrawingId);
  const setSelected = useChartUIStore((s) => s.setSelectedDrawing);
  const setHovered = useChartUIStore((s) => s.setHoveredDrawing);
  const toggleHidden = useChartUIStore((s) => s.toggleDrawingHidden);
  const toggleLock = useChartUIStore((s) => s.toggleDrawingLock);
  const remove = useChartUIStore((s) => s.removeDrawing);
  const bringForward = useChartUIStore((s) => s.bringDrawingForward);
  const sendBackward = useChartUIStore((s) => s.sendDrawingBackward);
  const close = useChartUIStore((s) => s.setShowLayersPanel);

  // Front-most (last painted) at the top of the list.
  const ordered = [...drawings].reverse();

  return (
    <div className="flex max-h-[70vh] w-64 flex-col rounded-lg border border-border-default bg-surface/95 shadow-2xl backdrop-blur-xl">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
        <div className="flex items-center gap-2">
          <LayersIcon size={14} className="text-primary" />
          <span className="text-xs font-semibold text-text-primary">Layers</span>
          <span className="rounded bg-elevated px-1.5 py-0.5 text-[9px] font-semibold tabular-nums text-text-muted">
            {drawings.length}
          </span>
        </div>
        <button
          type="button"
          onClick={() => close(false)}
          className="rounded p-0.5 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
          aria-label="Close layers"
        >
          <X size={14} />
        </button>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto scrollbar-none py-1">
        {ordered.length === 0 ? (
          <div className="flex flex-col items-center gap-1.5 px-4 py-8 text-center">
            <LayersIcon size={22} className="opacity-30" />
            <p className="text-[11px] text-text-muted">No drawings yet</p>
            <p className="text-[9px] text-text-muted/60">Use the drawing tools to add layers</p>
          </div>
        ) : (
          ordered.map((d) => {
            const isSelected = d.id === selectedId;
            return (
              <div
                key={d.id}
                onMouseEnter={() => setHovered(d.id)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => setSelected(d.id)}
                className={`group flex cursor-pointer items-center gap-1.5 px-2 py-1.5 transition-colors ${isSelected ? 'bg-primary/10' : 'hover:bg-elevated'}`}
              >
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full border border-black/30"
                  style={{ backgroundColor: d.color ?? '#2962FF' }}
                />
                <span
                  className={`flex-1 truncate text-[11px] ${d.hidden ? 'text-text-muted/50 line-through' : isSelected ? 'font-semibold text-primary' : 'text-text-secondary'}`}
                >
                  {toolLabel(d.tool)}
                </span>

                {/* Reorder */}
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); bringForward(d.id); }}
                  className="rounded p-0.5 text-text-muted/60 opacity-0 transition-colors hover:text-text-primary group-hover:opacity-100"
                  title="Bring forward"
                >
                  <ArrowUp size={12} />
                </button>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); sendBackward(d.id); }}
                  className="rounded p-0.5 text-text-muted/60 opacity-0 transition-colors hover:text-text-primary group-hover:opacity-100"
                  title="Send backward"
                >
                  <ArrowDown size={12} />
                </button>

                {/* Visibility */}
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); toggleHidden(d.id); }}
                  className="rounded p-0.5 text-text-muted transition-colors hover:text-text-primary"
                  title={d.hidden ? 'Show' : 'Hide'}
                >
                  {d.hidden ? <EyeOff size={13} /> : <Eye size={13} />}
                </button>

                {/* Lock */}
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); toggleLock(d.id); }}
                  className={`rounded p-0.5 transition-colors hover:text-text-primary ${d.locked ? 'text-primary' : 'text-text-muted'}`}
                  title={d.locked ? 'Unlock' : 'Lock'}
                >
                  {d.locked ? <Lock size={13} /> : <Unlock size={13} />}
                </button>

                {/* Delete */}
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); remove(d.id); }}
                  className="rounded p-0.5 text-text-muted transition-colors hover:text-red-400"
                  title="Delete"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
