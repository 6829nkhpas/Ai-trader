'use client';

import React, { useState } from 'react';
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
  User as UserIcon,
  LogOut,
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
} from 'lucide-react';
import { useTradeStore, TradeProfile } from '../../store/useTradeStore';
import { useAuth } from '../../context/AuthContext';
import { useChartUIStore } from '../../store/useChartUIStore';
import { ToolMenu, type ToolMenuEntry } from '../chart/ToolMenu';

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
  const { user, logout } = useAuth();
  const [isProfileOpen, setIsProfileOpen] = useState(false);
  
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
    clearDrawings
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

  const textOptions = [
    { id: 'text', label: 'Text', icon: Type },
    { id: 'callout', label: 'Callout', icon: MessageSquare },
    { id: 'price-label', label: 'Price Label', icon: Tag },
  ];

  const measurementOptions = [
    { id: 'long-position', label: 'Long Position', icon: ArrowUpRight },
    { id: 'short-position', label: 'Short Position', icon: ArrowDownRight },
    { id: 'price-range', label: 'Price Range', icon: Ruler },
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
                    ${
                      isActive
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
                    className={`rounded px-1 py-px text-[10px] font-medium leading-none ${
                      isActive
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

          <div className="relative">
            <button
              onClick={() => setIsProfileOpen(!isProfileOpen)}
              className="flex items-center gap-2 rounded-full border border-border-default bg-surface px-2 py-1 transition-colors hover:bg-elevated"
            >
              <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/20 text-primary">
                <UserIcon size={14} />
              </div>
              <ChevronDown size={14} className="text-text-secondary" />
            </button>

            {isProfileOpen && (
              <>
                <div 
                  className="fixed inset-0 z-40" 
                  onClick={() => setIsProfileOpen(false)}
                />
                
                <div className="absolute right-0 top-full z-50 mt-2 w-56 rounded-lg border border-border-default bg-surface py-2 shadow-lg panel-shadow">
                  <div className="border-b border-border-default px-4 pb-3 pt-2">
                    <p className="text-sm font-medium text-text-primary">
                      {user?.displayName || 'User'}
                    </p>
                    <p className="text-xs text-text-secondary truncate">
                      {user?.email || 'user@example.com'}
                    </p>
                  </div>
                  
                  <div className="flex flex-col py-1">
                    <button className="flex items-center gap-3 px-4 py-2 text-sm text-text-secondary hover:bg-elevated hover:text-text-primary text-left">
                      <HelpCircle size={16} />
                      Customer Support
                    </button>
                    <button 
                      onClick={async () => {
                        await logout();
                        setIsProfileOpen(false);
                      }}
                      className="flex items-center gap-3 px-4 py-2 text-sm text-red-500 hover:bg-elevated hover:text-red-400 text-left"
                    >
                      <LogOut size={16} />
                      Logout
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex flex-1 min-h-0 overflow-hidden bg-background p-2 gap-2">
        {/* Watchlist */}
        <aside className="flex w-56 shrink-0 min-h-0 flex-col overflow-y-auto border border-border-default rounded-lg bg-surface panel-shadow">
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
            icon={textOptions.find(o => o.id === activeDrawingTool)?.icon || Type}
            isActive={textOptions.some(o => o.id === activeDrawingTool)}
            options={textOptions}
            onSelect={setActiveDrawingTool}
          />

          <ToolMenu
            icon={measurementOptions.find(o => o.id === activeDrawingTool)?.icon || Ruler}
            isActive={measurementOptions.some(o => o.id === activeDrawingTool)}
            options={measurementOptions}
            onSelect={setActiveDrawingTool}
          />

          <div className="flex-grow" />

          {/* Standalone bottom buttons */}
          <div className="flex flex-col items-center gap-1.5 pt-2">
            <button
              type="button"
              onClick={cycleMagnetMode}
              title={`Magnet Mode: ${magnetMode}`}
              className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors ${
                magnetMode !== 'off'
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
              className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors ${
                drawingsLocked
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
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {children}
        </main>

      </div>
    </div>
  );
}
