'use client';

import React, { useId } from 'react';
import {
  Crosshair,
  TrendingUp,
  Brush,
  Type,
  Ruler,
  Magnet,
  Lock,
  Eye,
  Trash2,
  Hexagon,
  EyeOff,
  Layers,
  AlignEndHorizontal,
  ArrowUpRight,
} from 'lucide-react';
import { useChartUIStore } from '../../store/useChartUIStore';
import { ToolMenu, PremiumTooltip, toolDescriptions } from './ToolMenu';
import {
  cursorOptions,
  lineOptions,
  fibOptions,
  patternOptions,
  shapeOptions,
  textOptions,
  projectionOptions,
} from './chartToolOptions';

interface ChartToolsBarProps {
  className?: string;
}

export default function ChartToolsBar({ className = '' }: ChartToolsBarProps) {
  const {
    activeCursor,
    activeDrawingTool,
    magnetMode,
    drawingsVisible,
    drawingsLocked,
    setActiveCursor,
    setActiveDrawingTool,
    setMagnetMode,
    toggleDrawingsVisible,
    toggleDrawingsLocked,
    clearDrawings,
    drawingColor,
    setDrawingColor
  } = useChartUIStore();
  const showLayersPanel = useChartUIStore((s) => s.showLayersPanel);
  const toggleLayersPanel = useChartUIStore((s) => s.toggleLayersPanel);

  const colorPickerId = useId();

  const cycleMagnetMode = () => {
    if (magnetMode === 'off') setMagnetMode('weak');
    else if (magnetMode === 'weak') setMagnetMode('strong');
    else setMagnetMode('off');
  };

  return (
    <div className={`flex w-10 shrink-0 flex-col items-center bg-surface relative z-20 ${className}`}>
      {/* Cursor Select */}
      <ToolMenu
        icon={cursorOptions.find(o => o.id === activeCursor)?.icon || Crosshair}
        isActive={true}
        options={cursorOptions}
        onSelect={(id) => setActiveCursor(id as 'cross' | 'dot' | 'arrow' | 'eraser')}
      />

      {/* Lines Options */}
      <ToolMenu
        icon={(lineOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || TrendingUp}
        isActive={lineOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
        options={lineOptions}
        onSelect={setActiveDrawingTool}
      />

      {/* Fib Options */}
      <ToolMenu
        icon={(fibOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || AlignEndHorizontal}
        isActive={fibOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
        options={fibOptions}
        onSelect={setActiveDrawingTool}
      />

      {/* Pattern Options */}
      <ToolMenu
        icon={(patternOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || Hexagon}
        isActive={patternOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
        options={patternOptions}
        onSelect={setActiveDrawingTool}
      />

      {/* Shape Options */}
      <ToolMenu
        icon={(shapeOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || Brush}
        isActive={shapeOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
        options={shapeOptions}
        onSelect={setActiveDrawingTool}
      />

      {/* Text Options */}
      <ToolMenu
        icon={(textOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || Type}
        isActive={textOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
        options={textOptions}
        onSelect={setActiveDrawingTool}
      />

      {/* Projection Options */}
      <ToolMenu
        icon={(projectionOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || ArrowUpRight}
        isActive={projectionOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
        options={projectionOptions}
        onSelect={setActiveDrawingTool}
      />

      {/* Measure Tool */}
      <PremiumTooltip title="Measure Tool" content={toolDescriptions['measure']}>
        <button
          type="button"
          onClick={() => setActiveDrawingTool(activeDrawingTool === 'measure' ? null : 'measure')}
          className={`flex h-10 w-full items-center justify-center rounded-none transition-colors border-b border-border-default/30 ${activeDrawingTool === 'measure'
              ? 'text-emerald-400 bg-emerald-500/5'
              : 'text-text-secondary hover:bg-elevated/20 hover:text-text-primary'
            }`}
        >
          <Ruler size={15} />
        </button>
      </PremiumTooltip>

      {/* Color Picker */}
      <PremiumTooltip title="Drawing Color" content={toolDescriptions['color']}>
        <label
          htmlFor={colorPickerId}
          className="group relative flex h-10 w-full items-center justify-center rounded-none border-b border-border-default/30 cursor-pointer hover:bg-elevated/20 transition-colors"
        >
          <div 
            className="w-4 h-4 rounded-none border border-border-default/50 shadow-sm transition-transform group-hover:scale-110"
            style={{ backgroundColor: drawingColor }}
          />
          <input
            id={colorPickerId}
            type="color"
            value={drawingColor}
            onChange={(e) => setDrawingColor(e.target.value)}
            className="absolute opacity-0 w-0 h-0"
          />
        </label>
      </PremiumTooltip>

      {/* Layers Panel */}
      <PremiumTooltip title="Layers" content={toolDescriptions['layers']}>
        <button
          type="button"
          onClick={toggleLayersPanel}
          className={`flex h-10 w-full items-center justify-center rounded-none transition-colors border-b border-border-default/30 ${showLayersPanel
              ? 'text-emerald-400 bg-emerald-500/5'
              : 'text-text-secondary hover:bg-elevated/20 hover:text-text-primary'
            }`}
        >
          <Layers size={15} />
        </button>
      </PremiumTooltip>

      {/* Magnet Mode */}
      <PremiumTooltip title={`Magnet Mode: ${magnetMode.toUpperCase()}`} content={toolDescriptions[`magnet-${magnetMode}`] || toolDescriptions['magnet-off']}>
        <button
          type="button"
          onClick={cycleMagnetMode}
          className={`flex h-10 w-full items-center justify-center rounded-none transition-colors border-b border-border-default/30 ${magnetMode !== 'off'
              ? 'text-emerald-400 bg-emerald-500/5'
              : 'text-text-secondary hover:bg-elevated/20 hover:text-text-primary'
            }`}
        >
          <Magnet size={15} />
        </button>
      </PremiumTooltip>

      {/* Lock Drawings */}
      <PremiumTooltip title="Lock Drawings" content={toolDescriptions['lock']}>
        <button
          type="button"
          onClick={toggleDrawingsLocked}
          className={`flex h-10 w-full items-center justify-center rounded-none transition-colors border-b border-border-default/30 ${drawingsLocked
              ? 'text-emerald-400 bg-emerald-500/5'
              : 'text-text-secondary hover:bg-elevated/20 hover:text-text-primary'
            }`}
        >
          <Lock size={15} />
        </button>
      </PremiumTooltip>

      {/* Toggle Visibility */}
      <PremiumTooltip title={drawingsVisible ? 'Hide Drawings' : 'Show Drawings'} content={toolDescriptions['visible']}>
        <button
          type="button"
          onClick={toggleDrawingsVisible}
          className="flex h-10 w-full items-center justify-center rounded-none text-text-secondary transition-colors border-b border-border-default/30 hover:bg-elevated/20 hover:text-text-primary"
        >
          {drawingsVisible ? <Eye size={15} /> : <EyeOff size={15} />}
        </button>
      </PremiumTooltip>

      {/* Clear Drawings */}
      <PremiumTooltip title="Clear Drawings" content={toolDescriptions['clear']}>
        <button
          type="button"
          onClick={clearDrawings}
          className="flex h-10 w-full items-center justify-center rounded-none text-text-secondary transition-colors border-b border-border-default/30 hover:bg-elevated/20 hover:text-red-400"
        >
          <Trash2 size={15} />
        </button>
      </PremiumTooltip>
    </div>
  );
}
