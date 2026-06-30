'use client';

import React from 'react';
import { Loader2 } from 'lucide-react';

export default function WatchingIndicator() {
  return (
    <div className="flex justify-start animate-fade-in font-sans w-full my-1.5">
      <div className="max-w-[95%] bg-elevated/40 border border-border-default/50 rounded-none p-3 w-full flex flex-col gap-2.5 relative">
        {/* Left border accent */}
        <div className="absolute left-0 top-0 bottom-0 w-[2px] bg-amber-500/80" />

        {/* Header Row */}
        <div className="flex items-center justify-between pl-1">
          <div className="flex items-center gap-1.5 select-none">
            {/* Blinking Dot */}
            <span className="relative flex h-1.5 w-1.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-500/60 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-amber-500"></span>
            </span>
            <span className="text-[9px] text-text-primary font-bold uppercase tracking-wider">
              AI WATCHER
            </span>
          </div>

          <span className="text-[8px] font-mono font-bold bg-amber-500/10 text-amber-500 border border-amber-500/20 px-1.5 py-0.5 uppercase tracking-wide">
            Awaiting Trigger
          </span>
        </div>

        {/* Body / Message */}
        <div className="pl-1">
          <p className="text-[10px] text-text-secondary leading-normal">
            Execution paused. Monitoring market target boundaries in background.
          </p>
        </div>

        {/* Footer Row */}
        <div className="flex items-center justify-between pl-1 pt-1.5 border-t border-border-default/20 text-[8.5px] text-text-muted font-mono">
          <span className="truncate">watcher_daemon_active</span>
          <Loader2 size={9} className="animate-spin text-text-muted/50 shrink-0" />
        </div>
      </div>
    </div>
  );
}
