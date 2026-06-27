import React, { useState, useRef } from 'react';

interface ToolOption {
  id: string;
  label: string;
  icon: React.ElementType;
  shortcut?: string;
}

interface ToolSection {
  section: string;
}

export type ToolMenuEntry = ToolOption | ToolSection;

function isSection(entry: ToolMenuEntry): entry is ToolSection {
  return 'section' in entry;
}

// ── COMPREHENSIVE PREMIUM TOOL DESCRIPTIONS ─────────────────────────────
export const toolDescriptions: Record<string, string> = {
  // Cursors
  'cross': 'Crosshair cursor for precise coordinate alignments across price and time axes.',
  'dot': 'Dot pointer for cleaner chart presentations without grid line clutter.',
  'arrow': 'Standard arrow pointer for selecting, dragging elements, or directional indicator arrow for structural markings.',
  'eraser': 'Erase drawings or measurement overlays by clicking directly on them.',

  // Lines & Channels
  'trendline': 'Draw standard sloping lines to identify support, resistance, or structural trends.',
  'ray': 'An infinite single-direction ray to monitor long-term breakout zones.',
  'info-line': 'Shows angle, length, and coordinate differentials along the drawn line.',
  'extended-line': 'Extends infinitely in both directions to map key historical levels.',
  'trend-angle': 'Draws a line displaying the precise angular slope of market trends.',
  'horizontal-line': 'Plots a horizontal support or resistance line across the full chart.',
  'horizontal-ray': 'Draws a horizontal level extending only to the right of your click.',
  'vertical-line': 'Draws a vertical axis line to highlight specific temporal events.',
  'cross-line': 'Draws a combined horizontal and vertical intersection marker.',
  'parallel-channel': 'Plots parallel boundary lines representing trending price corridors.',
  'regression-trend': 'Calculates the linear regression center and standard deviation bands.',
  'flat-top-bottom': 'Highlights consolidations with absolute flat tops or bottoms.',
  'disjoint-channel': 'Plots dynamic channel boundaries with distinct off-set anchors.',

  // Fibonacci & Gann
  'fib-retracement': 'Calculates standard percentage levels of key market pullbacks.',
  'fib-extension': 'Projects future profit-taking targets based on three trend anchors.',
  'fib-channel': 'Applies Fibonacci proportions across sloping parallel channels.',
  'fib-time-zone': 'Identifies potential temporal turning points using Fibonacci spacing.',
  'fib-speed-fan': 'Applies Fibonacci percentage lines across angular fan charts.',
  'fib-time-trend': 'Projects future temporal cycles based on previous swing duration.',
  'fib-circles': 'Plots concentric circles highlighting circular support/resistance.',
  'fib-spiral': 'Applies the natural golden spiral ratio across the chart space.',
  'fib-arcs': 'Calculates speed-resistance arcs across historical peaks and valleys.',
  'fib-wedge': 'Plots wedge boundaries with progressive Fibonacci divisions.',
  'pitchfan': 'Draws angular fan rays based on standard pitchfork centerlines.',
  'gann-box': 'Maps complex price-and-time geometries over market cycles.',
  'gann-square-fixed': 'Applies static Gann intervals across price and time axes.',
  'gann-square': 'Draws standard Gann squares matching symmetrical cycle counts.',
  'gann-fan': 'Plots angular geometric vectors from a single key pivot point.',

  // Patterns & Waves
  'xabcd-pattern': 'Maps classic harmonic patterns (Gartley, Butterfly, Bat).',
  'cypher-pattern': 'Maps the advanced harmonic Cypher breakout/reversal pattern.',
  'head-shoulders': 'Highlights Head and Shoulders trend exhaustion patterns.',
  'abcd-pattern': 'Draws measured-move symmetrical swing patterns.',
  'triangle-pattern': 'Plots consolidation triangles for breakout prediction.',
  'three-drives': 'Identifies three consecutive exhaustion spikes in active trends.',
  'elliott-impulse': 'Highlights the standard 5-wave impulse motive structure (1-2-3-4-5).',
  'elliott-correction': 'Traces standard 3-wave Elliott correction cycles (A-B-C).',
  'elliott-triangle': 'Maps consolidating horizontal correction structures (A-B-C-D-E).',
  'elliott-double-combo': 'Plots complex double-three market corrections (W-X-Y).',
  'elliott-triple-combo': 'Plots extended triple-three corrective cycles (W-X-Y-X-Z).',
  'cyclic-lines': 'Applies equidistant lines to identify periodic cycle frequencies.',
  'time-cycles': 'Draws temporal half-circles measuring cyclic wavelengths.',
  'sine-line': 'Plots continuous sine wave overlays to model cyclical trends.',

  // Brushes & Shapes
  'brush': 'Freehand drawing tool for marking structures or paths directly on screen.',
  'highlighter': 'Semi-transparent brush to draw focus to specific market candles.',
  'arrow-marker': 'Places custom indicator flags directly over key chart positions.',
  'arrow-mark-up': 'Draws large bullish indicator arrows pointing upwards.',
  'arrow-mark-down': 'Draws large bearish indicator arrows pointing downwards.',
  'arrow-mark-left': 'Draws an indicator arrow pointing towards left context.',
  'arrow-mark-right': 'Draws an indicator arrow pointing towards future timelines.',
  'rectangle': 'Highlights regional blocks like order blocks or supply/demand zones.',
  'rotated-rectangle': 'Draws angled rectangular zones that align to sloping channels.',
  'path': 'Draws complex multi-anchor paths to map wave corridors.',
  'circle': 'Highlights crucial singular price pivots or chart formations.',
  'ellipse': 'Draws extended oval zones to map cluster concentrations.',
  'polyline': 'Plots multi-segment straight lines for structural markings.',
  'triangle-shape': 'Draws triangle geometry to highlight geometric patterns.',
  'arc': 'Draws curved arcs to highlight rounded bottoms or cup formations.',
  'curve': 'Draws a smooth curve anchor through three points.',
  'double-curve': 'Draws complex S-curves across two control pivots.',

  // Text & Notes
  'text': 'Adds customizable, standard floating labels to your chart.',
  'anchored-text': 'Adds text locked to absolute screen pixels, unaffected by zooms.',
  'note': 'Adds hover-expandable note pins directly to selected candles.',
  'anchored-note': 'Pins permanent, hoverable screen notes at static coordinates.',
  'callout': 'Draws text boxes with connector pointers targeting specific elements.',
  'comment': 'Adds inline commentary markers to record trade logic context.',
  'price-label': 'Draws a price tag that dynamically matches its vertical coordinate.',
  'price-note': 'Displays dynamic coordinate values in a styled information block.',
  'signpost': 'Places visual milestone indicators directly along the timeline.',
  'flag-mark': 'Highlights key temporal zones with colored operational flags.',

  // Projection & Volume
  'long-position': 'Calculates Risk/Reward ratios, targets, and stops for long trades.',
  'short-position': 'Calculates Risk/Reward ratios, targets, and stops for short trades.',
  'forecast': 'Plots expected future path projections with custom success metrics.',
  'bars-pattern': 'Copies historical candle sequences to overlay elsewhere as templates.',
  'ghost-feed': 'Visualizes mock future candle runs to test trading thesis paths.',
  'projection': 'Plots future price vector corridors matching volume velocity.',
  'anchored-vwap': 'Calculates Volume Weighted Average Price from a custom anchor point.',
  'fixed-range-volume': 'Displays the Volume Profile histogram over a chosen region.',
  'price-range': 'Measures absolute and percentage price differentials.',
  'date-range': 'Measures duration, calendar days, and bars between selected dates.',
  'date-price-range': 'Measures combined price movements and time durations.',

  // Standalone Toolbar Utilities
  'measure': 'Ruler utility to measure price distance, percentage changes, and time intervals.',
  'color': 'Select the active primary stroke color for all future drawing actions.',
  'magnet-weak': 'Snaps drawing anchors slightly to the nearest candle high, low, or close.',
  'magnet-strong': 'Snaps drawing anchors forcefully to the nearest candle coordinates.',
  'magnet-off': 'Disables snapping behaviors. Drawing anchors place strictly on cursor coordinates.',
  'lock': 'Locks all active chart drawings to prevent accidental drag movements.',
  'visible': 'Toggle visibility: hide or show all drawings and markers instantly.',
  'clear': 'Permanently delete all active chart drawing overlays.',
  'layers': 'Open the Layers panel to select, reorder, hide, lock, or delete each drawing individually.'
};

// ── GENERAL PURPOSE PREMIUM TOOLTIP WRAPPER ─────────────────────────────
interface PremiumTooltipProps {
  children: React.ReactElement;
  content: string;
  title?: string;
  shortcut?: string;
  position?: 'right' | 'left' | 'top' | 'bottom';
}

export function PremiumTooltip({ children, content, title, shortcut, position = 'right' }: PremiumTooltipProps) {
  const [show, setShow] = useState(false);
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  const onEnter = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    setShow(true);
  };

  const onLeave = () => {
    timeoutRef.current = setTimeout(() => {
      setShow(false);
    }, 100);
  };

  const positionClasses = {
    right: 'left-full top-1/2 -translate-y-1/2 ml-2',
    left: 'right-full top-1/2 -translate-y-1/2 mr-2',
    top: 'bottom-full left-1/2 -translate-x-1/2 mb-2',
    bottom: 'top-full left-1/2 -translate-x-1/2 mt-2',
  };

  return (
    <div className="relative flex items-center justify-center" onMouseEnter={onEnter} onMouseLeave={onLeave}>
      {children}
      {show && (
        <div className={`absolute z-[100] w-60 rounded-none border border-border-default/80 bg-card/95 p-3 shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 duration-150 ${positionClasses[position]}`}>
          {title && (
            <div className="flex items-center justify-between border-b border-border-default/40 pb-1.5 mb-1.5">
              <span className="text-xs font-bold text-text-primary tracking-wide">{title}</span>
              {shortcut && (
                <span className="rounded bg-elevated/80 px-1 py-0.5 font-mono text-[9px] font-semibold text-emerald-600 dark:text-emerald-400">
                  {shortcut}
                </span>
              )}
            </div>
          )}
          <p className="text-[11px] leading-relaxed text-text-secondary font-medium">{content}</p>
        </div>
      )}
    </div>
  );
}

// ── INTERACTIVE DROPDOWN WITH DETAILS CARD ──────────────────────────────
interface ToolMenuProps {
  icon: React.ElementType;
  isActive: boolean;
  options: ToolMenuEntry[];
  onSelect: (id: string) => void;
}

export function ToolMenu({ icon: Icon, isActive, options, onSelect }: ToolMenuProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [hoveredOption, setHoveredOption] = useState<ToolOption | null>(null);
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  const handleMouseEnter = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    setIsOpen(true);
  };

  const handleMouseLeave = () => {
    timeoutRef.current = setTimeout(() => {
      setIsOpen(false);
      setHoveredOption(null);
    }, 150);
  };

  return (
    <div
      className="relative flex items-center justify-center w-full"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <button
        type="button"
        className={`flex h-8 w-8 items-center justify-center rounded-md transition-colors ${isActive
            ? 'text-primary bg-primary/10'
            : isOpen
              ? 'text-text-primary bg-elevated'
              : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
          }`}
      >
        <Icon size={15} />
      </button>

      {isOpen && (
        <div className="absolute left-full top-0 z-50 ml-1.5 flex gap-1.5 items-start">
          {/* Submenu Options List */}
          <div className="w-56 max-h-[70vh] overflow-y-auto overscroll-contain rounded-none border border-border-default bg-surface shadow-lg panel-shadow py-1 scrollbar-none">
            {options.map((entry, idx) => {
              if (isSection(entry)) {
                return (
                  <div key={`section-${idx}`} className="px-3 pt-3 pb-1.5">
                    <span className="text-[10px] font-semibold uppercase tracking-widest text-text-secondary/50">
                      {entry.section}
                    </span>
                  </div>
                );
              }

              const OptionIcon = entry.icon;
              return (
                <button
                  key={entry.id}
                  onClick={() => {
                    onSelect(entry.id);
                    setIsOpen(false);
                  }}
                  onMouseEnter={() => setHoveredOption(entry)}
                  onMouseLeave={() => setHoveredOption(null)}
                  className="flex w-full items-center gap-3 px-3 py-1.5 text-sm text-text-secondary hover:bg-elevated hover:text-text-primary text-left"
                >
                  <OptionIcon size={14} className="shrink-0 text-text-secondary/80 group-hover:text-text-primary" />
                  <span className="flex-1 truncate">{entry.label}</span>
                  {entry.shortcut && (
                    <span className="text-[10px] font-mono text-text-secondary/40">{entry.shortcut}</span>
                  )}
                </button>
              );
            })}
          </div>

          {/* Interactive Symmetrical Detail Hover Card */}
          {hoveredOption && (
            <div className="w-64 shrink-0 rounded-none border border-border-default bg-card/95 p-3.5 shadow-2xl backdrop-blur-md animate-in fade-in zoom-in-95 duration-100 flex flex-col gap-2">
              <div className="flex items-center justify-between border-b border-border-default/40 pb-1.5">
                <div className="flex items-center gap-2 min-w-0">
                  {React.createElement(hoveredOption.icon, { size: 14, className: "text-emerald-600 dark:text-emerald-400 shrink-0" })}
                  <span className="text-xs font-bold text-text-primary tracking-wide truncate">{hoveredOption.label}</span>
                </div>
                {hoveredOption.shortcut && (
                  <span className="rounded bg-elevated px-1 py-0.5 font-mono text-[9px] font-semibold text-emerald-600 dark:text-emerald-400 shrink-0">
                    {hoveredOption.shortcut}
                  </span>
                )}
              </div>
              <p className="text-[11px] leading-relaxed text-text-secondary font-medium">
                {toolDescriptions[hoveredOption.id] || "Draw and project structural patterns on the charting canvas."}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
