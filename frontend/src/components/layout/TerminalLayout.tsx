'use client';

import React, { useState } from 'react';
import dynamic from 'next/dynamic';
import {
  Activity,
  RefreshCcw,
  Crosshair,
  TrendingUp,
  Minus,
  Brush,
  Type,
  Ruler,
  Magnet,
  Lock,
  Eye,
  Trash2,
  Bell,
  HelpCircle,
  ChevronDown,
  Circle,
  MousePointer2,
  Eraser,
  ArrowRight,
  Plus,
  AlignEndHorizontal,
  AlignCenterHorizontal,
  Highlighter,
  Square,
  MessageSquare,
  Tag,
  ArrowUpRight,
  ArrowDownRight,
  EyeOff,
  MoveVertical,
  MoveRight,
  Info,
  MoveHorizontal,
  CornerRightUp,
  Columns,
  TrendingDown,
  ChevronsUpDown,
  Unlink,
  Layers,
  Clock,
  Wind,
  Timer,
  Target,
  RotateCcw,
  Sparkles,
  Triangle,
  Waypoints,
  Grid3x3,
  Hash,
  Scan,
  Fan,
  // Pattern & Wave & Cycle icons
  Hexagon,
  Pentagon,
  Gem,
  Diamond,
  Shapes,
  GitBranch,
  GitMerge,
  GitPullRequest,
  GitCommit,
  Workflow,
  BarChart3,
  AudioWaveform,
  Waves,
  // Brush / Arrow / Shape icons
  ArrowBigUp,
  ArrowBigDown,
  ArrowBigLeft,
  ArrowBigRight,
  Navigation,
  MapPin,
  RotateCw,
  Spline,
  PenTool,
  Sigma,
  Orbit,
  Slice,
  Radical,
  // Projection icons
  Crosshair as CrosshairIcon,
  BarChart2,
  Rss,
  LineChart,
  Anchor,
  BarChart4,
  Calendar,
  CalendarRange,
  // Text icons
  StickyNote,
  MessageCircle,
  Monitor,
  Signpost,
  Flag,
  // Settings
  Settings,
  X as XIcon,
  Shield,
} from 'lucide-react';
import { useTradeStore, TradeProfile } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import { ToolMenu, type ToolMenuEntry } from '../chart/ToolMenu';
import QuantRadar from '../quant/QuantRadar';

// SSR-disabled dynamic import: Tauri plugins (Stronghold, Path API) are only
// available in the desktop WebView. Loading them during Next.js server render
// triggers `Module not found: Can't resolve '@tauri-apps/plugin-stronghold'`.
// `{ ssr: false }` ensures this component is mounted strictly on the client.
const SecurityVault = dynamic(
  () => import('../settings/SecurityVault'),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-32 items-center justify-center text-[10px] text-text-muted">
        Loading vault…
      </div>
    ),
  }
);

const PROFILES: { key: TradeProfile; label: string; shortcut: string }[] = [
  { key: 'INTRADAY', label: 'Intraday', shortcut: 'Scalp' },
  { key: 'SWING', label: 'Swing', shortcut: '1H-4H' },
  { key: 'INVESTOR', label: 'Investor', shortcut: 'Macro' },
];

interface TerminalLayoutProps {
  children: React.ReactNode;
  leftPanel: React.ReactNode;
}

export default function TerminalLayout({ children, leftPanel }: TerminalLayoutProps) {
  const { activeProfile, setActiveProfile, resetSession } = useTradeStore();
  const [settingsOpen, setSettingsOpen] = useState(false);

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

  const cursorOptions = [
    { id: 'cross', label: 'Crosshair', icon: Crosshair },
    { id: 'dot', label: 'Dot', icon: Circle },
    { id: 'arrow', label: 'Arrow', icon: MousePointer2 },
    { id: 'eraser', label: 'Eraser', icon: Eraser },
  ];

  const lineOptions: ToolMenuEntry[] = [
    { id: 'trendline', label: 'Trend Line', icon: TrendingUp, shortcut: 'Alt + T' },
    { id: 'ray', label: 'Ray', icon: MoveRight },
    { id: 'info-line', label: 'Info Line', icon: Info },
    { id: 'extended-line', label: 'Extended Line', icon: MoveHorizontal },
    { id: 'trend-angle', label: 'Trend Angle', icon: CornerRightUp },
    { id: 'horizontal-line', label: 'Horizontal Line', icon: Minus, shortcut: 'Alt + H' },
    { id: 'horizontal-ray', label: 'Horizontal Ray', icon: ArrowRight, shortcut: 'Alt + J' },
    { id: 'vertical-line', label: 'Vertical Line', icon: MoveVertical, shortcut: 'Alt + V' },
    { id: 'cross-line', label: 'Cross Line', icon: Plus, shortcut: 'Alt + C' },
    { section: 'Channels' },
    { id: 'parallel-channel', label: 'Parallel Channel', icon: Columns },
    { id: 'regression-trend', label: 'Regression Trend', icon: TrendingDown },
    { id: 'flat-top-bottom', label: 'Flat Top/Bottom', icon: ChevronsUpDown },
    { id: 'disjoint-channel', label: 'Disjoint Channel', icon: Unlink },
  ];

  const fibOptions: ToolMenuEntry[] = [
    { section: 'Fibonacci' },
    { id: 'fib-retracement', label: 'Fib Retracement', icon: AlignEndHorizontal, shortcut: 'Alt + F' },
    { id: 'fib-extension', label: 'Trend-Based Fib Extension', icon: AlignCenterHorizontal },
    { id: 'fib-channel', label: 'Fib Channel', icon: Layers },
    { id: 'fib-time-zone', label: 'Fib Time Zone', icon: Clock },
    { id: 'fib-speed-fan', label: 'Fib Speed Resistance Fan', icon: Wind },
    { id: 'fib-time-trend', label: 'Trend-Based Fib Time', icon: Timer },
    { id: 'fib-circles', label: 'Fib Circles', icon: Target },
    { id: 'fib-spiral', label: 'Fib Spiral', icon: RotateCcw },
    { id: 'fib-arcs', label: 'Fib Speed Resistance Arcs', icon: Sparkles },
    { id: 'fib-wedge', label: 'Fib Wedge', icon: Triangle },
    { id: 'pitchfan', label: 'Pitchfan', icon: Waypoints },
    { section: 'Gann' },
    { id: 'gann-box', label: 'Gann Box', icon: Grid3x3 },
    { id: 'gann-square-fixed', label: 'Gann Square Fixed', icon: Scan },
    { id: 'gann-square', label: 'Gann Square', icon: Hash },
    { id: 'gann-fan', label: 'Gann Fan', icon: Fan },
  ];

  const patternOptions: ToolMenuEntry[] = [
    { section: 'Patterns' },
    { id: 'xabcd-pattern', label: 'XABCD Pattern', icon: Hexagon },
    { id: 'cypher-pattern', label: 'Cypher Pattern', icon: Pentagon },
    { id: 'head-shoulders', label: 'Head and Shoulders', icon: Gem },
    { id: 'abcd-pattern', label: 'ABCD Pattern', icon: Diamond },
    { id: 'triangle-pattern', label: 'Triangle Pattern', icon: Triangle },
    { id: 'three-drives', label: 'Three Drives Pattern', icon: Shapes },
    { section: 'Elliott Waves' },
    { id: 'elliott-impulse', label: 'Elliott Impulse Wave (12345)', icon: GitBranch },
    { id: 'elliott-correction', label: 'Elliott Correction Wave (ABC)', icon: GitMerge },
    { id: 'elliott-triangle', label: 'Elliott Triangle Wave (ABCDE)', icon: GitPullRequest },
    { id: 'elliott-double-combo', label: 'Elliott Double Combo (WXY)', icon: GitCommit },
    { id: 'elliott-triple-combo', label: 'Elliott Triple Combo (WXYXZ)', icon: Workflow },
    { section: 'Cycles' },
    { id: 'cyclic-lines', label: 'Cyclic Lines', icon: BarChart3 },
    { id: 'time-cycles', label: 'Time Cycles', icon: AudioWaveform },
    { id: 'sine-line', label: 'Sine Line', icon: Waves },
  ];

  const shapeOptions: ToolMenuEntry[] = [
    { section: 'Brushes' },
    { id: 'brush', label: 'Brush', icon: Brush },
    { id: 'highlighter', label: 'Highlighter', icon: Highlighter },
    { section: 'Arrows' },
    { id: 'arrow-marker', label: 'Arrow Marker', icon: MapPin },
    { id: 'arrow', label: 'Arrow', icon: Navigation },
    { id: 'arrow-mark-up', label: 'Arrow Mark Up', icon: ArrowBigUp },
    { id: 'arrow-mark-down', label: 'Arrow Mark Down', icon: ArrowBigDown },
    { id: 'arrow-mark-left', label: 'Arrow Mark Left', icon: ArrowBigLeft },
    { id: 'arrow-mark-right', label: 'Arrow Mark Right', icon: ArrowBigRight },
    { section: 'Shapes' },
    { id: 'rectangle', label: 'Rectangle', icon: Square, shortcut: 'Alt + Shift + R' },
    { id: 'rotated-rectangle', label: 'Rotated Rectangle', icon: RotateCw },
    { id: 'path', label: 'Path', icon: PenTool },
    { id: 'circle', label: 'Circle', icon: Circle },
    { id: 'ellipse', label: 'Ellipse', icon: Orbit },
    { id: 'polyline', label: 'Polyline', icon: Spline },
    { id: 'triangle-shape', label: 'Triangle', icon: Triangle },
    { id: 'arc', label: 'Arc', icon: Slice },
    { id: 'curve', label: 'Curve', icon: Sigma },
    { id: 'double-curve', label: 'Double Curve', icon: Radical },
  ];

  const textOptions: ToolMenuEntry[] = [
    { section: 'Text & Notes' },
    { id: 'text', label: 'Text', icon: Type },
    { id: 'anchored-text', label: 'Anchored Text', icon: Anchor },
    { id: 'note', label: 'Note', icon: StickyNote },
    { id: 'anchored-note', label: 'Anchored Note', icon: MapPin },
    { id: 'callout', label: 'Callout', icon: MessageSquare },
    { id: 'comment', label: 'Comment', icon: MessageCircle },
    { id: 'price-label', label: 'Price Label', icon: Tag },
    { id: 'price-note', label: 'Price Note', icon: Monitor },
    { id: 'signpost', label: 'Signpost', icon: Signpost },
    { id: 'flag-mark', label: 'Flag Mark', icon: Flag },
  ];

  const projectionOptions: ToolMenuEntry[] = [
    { section: 'Projection' },
    { id: 'long-position', label: 'Long Position', icon: ArrowUpRight },
    { id: 'short-position', label: 'Short Position', icon: ArrowDownRight },
    { id: 'forecast', label: 'Forecast', icon: LineChart },
    { id: 'bars-pattern', label: 'Bars Pattern', icon: BarChart2 },
    { id: 'ghost-feed', label: 'Ghost Feed', icon: Rss },
    { id: 'projection', label: 'Projection', icon: TrendingUp },
    { section: 'Volume-Based' },
    { id: 'anchored-vwap', label: 'Anchored VWAP', icon: Anchor },
    { id: 'fixed-range-volume', label: 'Fixed Range Volume Profile', icon: BarChart4 },
    { section: 'Measurer' },
    { id: 'price-range', label: 'Price Range', icon: Ruler },
    { id: 'date-range', label: 'Date Range', icon: Calendar },
    { id: 'date-price-range', label: 'Date and Price Range', icon: CalendarRange },
  ];

  const cycleMagnetMode = () => {
    if (magnetMode === 'off') setMagnetMode('weak');
    else if (magnetMode === 'weak') setMagnetMode('strong');
    else setMagnetMode('off');
  };

  return (
    <div className="flex h-screen flex-col bg-background font-sans text-text-primary">
      {/* Header */}
      <header className="z-10 flex shrink-0 items-center gap-3 border-b border-border-default bg-surface px-3 py-1.5 panel-shadow-sm">
        <div className="flex flex-1 items-center gap-3">
          <Activity className="text-primary" size={22} />
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-text-primary">AI-TRADE TERMINAL</h1>
            <p className="text-xs text-text-secondary">Live market decisions, signal flow, and execution review</p>
          </div>
        </div>

        {/* ── Segmented Profile Control ──────────────────────── */}
        <div className="flex shrink-0 items-center justify-center">
          <div className="flex items-center gap-1 rounded-lg border border-border-default bg-surface p-0.5 shadow-sm">
            {PROFILES.map(({ key, label, shortcut }) => {
              const isActive = activeProfile === key;
              return (
                <button
                  key={key}
                  id={`profile-btn-${key.toLowerCase()}`}
                  type="button"
                  onClick={() => setActiveProfile(key)}
                  className={`
                    relative flex items-center gap-1.5 rounded-md px-3.5 py-1.5 text-xs font-semibold
                    transition-all duration-200 ease-out select-none
                    focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/60
                    ${isActive
                      ? 'bg-emerald-500/15 text-emerald-400'
                      : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                    }
                  `}
                >
                  {/* Active glow dot */}
                  {isActive && (
                    <span className="absolute -top-px right-2 h-1.5 w-1.5 rounded-full bg-[#059669]" />
                  )}
                  <span>{label}</span>
                  <span
                    className={`rounded px-1 py-px text-[10px] font-medium leading-none ${isActive
                        ? 'bg-emerald-500/10 text-[#059669]'
                        : 'bg-elevated text-text-secondary'
                      }`}
                  >
                    {shortcut}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="flex flex-1 items-center justify-end gap-4 relative">
          <button
            onClick={resetSession}
            className="flex items-center gap-2 rounded-full border border-border-default bg-card px-3 py-1.5 text-xs font-semibold text-text-secondary transition-colors hover:bg-elevated mr-2"
            title="Reset Session and Clear Orders"
          >
            <RefreshCcw size={14} />
            Reset Session
          </button>

          <button className="relative text-text-secondary hover:text-text-primary transition-colors">
            <Bell size={18} />
            <span className="absolute top-0 right-0 h-2 w-2 rounded-full bg-red-500 border border-surface"></span>
          </button>

          {/* Settings — Security Vault */}
          <button
            type="button"
            id="settings-security-vault-btn"
            onClick={() => setSettingsOpen(true)}
            className="flex items-center gap-1.5 rounded-full border border-violet-500/30 bg-violet-500/8 px-3 py-1 text-xs font-medium text-violet-400 transition-all hover:bg-violet-500/15 hover:border-violet-500/50"
            title="Security Vault — API Key Management"
          >
            <Shield size={13} />
            <span className="hidden sm:inline">Vault</span>
          </button>

          <button
            type="button"
            className="flex items-center gap-2 rounded-full border border-border-default bg-surface px-3 py-1 text-xs font-medium text-text-secondary transition-colors hover:bg-elevated"
            title="Help"
          >
            <HelpCircle size={14} />
            <span className="hidden sm:inline">Help</span>
          </button>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex flex-1 min-h-0 overflow-visible bg-background p-2 gap-2">
        {/* Watchlist */}
        <aside className="flex w-56 shrink-0 min-h-0 flex-col overflow-visible border border-border-default rounded-lg bg-surface panel-shadow">
          {leftPanel}
        </aside>

        {/* Tools Bar */}
        <div className="flex w-12 shrink-0 flex-col items-center gap-1.5 border border-border-default rounded-lg bg-surface py-2 panel-shadow relative z-20">

          <ToolMenu
            icon={cursorOptions.find(o => o.id === activeCursor)?.icon || Crosshair}
            isActive={true}
            options={cursorOptions}
            onSelect={(id) => setActiveCursor(id as 'cross' | 'dot' | 'arrow' | 'eraser')}
          />

          <div className="my-1 h-px w-6 bg-border-default/50" />

          <ToolMenu
            icon={(lineOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || TrendingUp}
            isActive={lineOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
            options={lineOptions}
            onSelect={setActiveDrawingTool}
          />

          <ToolMenu
            icon={(fibOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || AlignEndHorizontal}
            isActive={fibOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
            options={fibOptions}
            onSelect={setActiveDrawingTool}
          />

          <ToolMenu
            icon={(patternOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || Hexagon}
            isActive={patternOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
            options={patternOptions}
            onSelect={setActiveDrawingTool}
          />

          <ToolMenu
            icon={(shapeOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || Brush}
            isActive={shapeOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
            options={shapeOptions}
            onSelect={setActiveDrawingTool}
          />

          <ToolMenu
            icon={(textOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || Type}
            isActive={textOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
            options={textOptions}
            onSelect={setActiveDrawingTool}
          />

          <ToolMenu
            icon={(projectionOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).find(o => o.id === activeDrawingTool))?.icon || ArrowUpRight}
            isActive={projectionOptions.filter((o): o is { id: string; label: string; icon: React.ElementType; shortcut?: string } => 'id' in o).some(o => o.id === activeDrawingTool)}
            options={projectionOptions}
            onSelect={setActiveDrawingTool}
          />

          <div className="flex flex-col items-center gap-1.5 pb-2 border-b border-border-default w-full mb-2">
            <button
              type="button"
              onClick={() => setActiveDrawingTool(activeDrawingTool === 'measure' ? null : 'measure')}
              title="Measure"
              className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors ${activeDrawingTool === 'measure'
                  ? 'text-primary bg-primary/10'
                  : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                }`}
            >
              <Ruler size={16} />
            </button>
          </div>

          {/* Color Picker */}
          <div className="flex flex-col items-center pb-2 border-b border-border-default w-full">
            <label
              htmlFor="color-picker"
              className="group relative flex h-8 w-8 items-center justify-center rounded-md cursor-pointer hover:bg-elevated transition-colors"
              title="Drawing Color"
            >
              <div 
                className="w-5 h-5 rounded-full border border-border-default/50 shadow-sm transition-transform group-hover:scale-110"
                style={{ backgroundColor: drawingColor }}
              />
              <input
                id="color-picker"
                type="color"
                value={drawingColor}
                onChange={(e) => setDrawingColor(e.target.value)}
                className="absolute opacity-0 w-0 h-0"
              />
            </label>
          </div>

          {/* Standalone bottom buttons */}
          <div className="flex flex-col items-center gap-1.5 pt-2">
            <button
              type="button"
              onClick={cycleMagnetMode}
              title={`Magnet Mode: ${magnetMode}`}
              className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors ${magnetMode !== 'off'
                  ? 'text-primary bg-primary/10'
                  : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                }`}
            >
              <Magnet size={15} />
            </button>

            <button
              type="button"
              onClick={toggleDrawingsLocked}
              title="Lock All Drawings"
              className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors ${drawingsLocked
                  ? 'text-primary bg-primary/10'
                  : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                }`}
            >
              <Lock size={15} />
            </button>

            <button
              type="button"
              onClick={toggleDrawingsVisible}
              title={drawingsVisible ? 'Hide Drawings' : 'Show Drawings'}
              className="flex h-8 w-8 items-center justify-center rounded-md text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
            >
              {drawingsVisible ? <Eye size={15} /> : <EyeOff size={15} />}
            </button>

            <button
              type="button"
              onClick={clearDrawings}
              title="Clear Drawings"
              className="flex h-8 w-8 items-center justify-center rounded-md text-text-secondary transition-colors hover:bg-elevated hover:text-red-400"
            >
              <Trash2 size={15} />
            </button>
          </div>
        </div>

        {/* Central Area */}
        <main className="flex min-h-0 flex-1 flex-col overflow-visible">
          {children}
        </main>

      </div>

      {/* ── Quant Radar Overlay ──────────────────────────────────── */}
      <QuantRadar />

      {/* ── Security Vault Settings Modal ────────────────────────── */}
      {settingsOpen && (
        <div
          className="fixed inset-0 z-[9999] flex items-start justify-end"
          role="dialog"
          aria-modal="true"
          aria-label="Security Vault Settings"
        >
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setSettingsOpen(false)}
          />

          {/* Slide-in Panel */}
          <div
            className="relative z-10 flex h-full w-[380px] max-w-[95vw] flex-col border-l border-border-default shadow-2xl overflow-hidden"
            style={{
              background: 'var(--color-surface)',
              animation: 'slideInRight 0.25s cubic-bezier(0.16, 1, 0.3, 1)',
            }}
          >
            {/* Panel Header */}
            <div className="flex shrink-0 items-center gap-2 border-b border-border-default px-4 py-3" style={{ background: 'var(--color-surface)' }}>
              <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-violet-500/15 border border-violet-500/30">
                <Shield size={14} className="text-violet-400" />
              </div>
              <div>
                <h2 className="text-sm font-bold text-text-primary tracking-tight">Security Vault</h2>
                <p className="text-[9px] text-text-muted">Encrypted credential management · AES-256</p>
              </div>
              <button
                type="button"
                onClick={() => setSettingsOpen(false)}
                className="ml-auto flex h-7 w-7 items-center justify-center rounded-lg border border-border-default text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
                aria-label="Close settings"
              >
                <XIcon size={14} />
              </button>
            </div>

            {/* Panel Body */}
            <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin">
              <SecurityVault />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
