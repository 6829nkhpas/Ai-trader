'use client';

import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Wifi, WifiOff, Server, RefreshCw, AlertTriangle, ServerCrash } from 'lucide-react';
import { useTradeStore } from '../../store/useTradeStore';
import { hoverScale, fadeInUp } from '../../lib/motionVariants';

export default function ConnectionLost() {
  const { wsStatus, connectWebSocket } = useTradeStore();
  const [isOnline, setIsOnline] = useState(true);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const updateStatus = () => {
      setIsOnline(navigator.onLine);
    };

    window.addEventListener('online', updateStatus);
    window.addEventListener('offline', updateStatus);

    // Set initial
    setIsOnline(navigator.onLine);

    return () => {
      window.removeEventListener('online', updateStatus);
      window.removeEventListener('offline', updateStatus);
    };
  }, []);

  const handleRetry = async () => {
    setRetrying(true);
    // Trigger reconnection
    connectWebSocket();
    // Simulate manual feedback
    setTimeout(() => {
      setRetrying(false);
    }, 1200);
  };

  const serverConnected = wsStatus === 'connected';
  const serverConnecting = wsStatus === 'connecting';

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-surface/90 backdrop-blur-md select-none transition-all duration-300">
      {/* Ambient brand glow (emerald) */}
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-[28rem] w-[28rem] -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/5 blur-[130px]" />

      {/* Main card */}
      <motion.div
        variants={fadeInUp}
        initial="hidden"
        animate="show"
        className="relative w-full max-w-md overflow-hidden rounded-2xl border border-border-default bg-elevated p-8 shadow-2xl flex flex-col items-center text-center"
      >
        {/* Accent top hairline */}
        <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/60 to-transparent" />

        {/* Icon emblem */}
        <div className="relative mb-6 flex items-center justify-center">
          <div className="absolute h-28 w-28 rounded-full bg-primary/10 blur-xl" />
          <div className="relative flex h-20 w-20 items-center justify-center rounded-2xl border border-border-default bg-surface">
            <ServerCrash size={32} strokeWidth={1.5} className="text-text-secondary" />
            <span className="absolute -right-2 -top-2 flex h-7 w-7 items-center justify-center rounded-full border border-border-default bg-elevated">
              <AlertTriangle size={13} className="text-amber-400" />
            </span>
          </div>
        </div>

        {/* Title */}
        <h2 className="mb-2 text-xl font-black tracking-tight text-text-primary">
          Connectivity Interrupted
        </h2>
        <p className="mb-6 max-w-sm text-xs leading-relaxed text-text-secondary">
          We detected a disruption in your terminal feed. Please verify your internet connection or
          check if the trading server is reachable.
        </p>

        {/* Status indicators */}
        <div className="mb-7 grid w-full max-w-xs grid-cols-2 gap-3">
          {/* Internet */}
          <div className="flex flex-col items-center rounded-xl border border-border-default bg-surface p-3">
            <div className="mb-1 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-text-muted">
              {isOnline ? <Wifi size={11} className="text-primary" /> : <WifiOff size={11} className="text-rose-400" />}
              Internet
            </div>
            <span className={`text-xs font-extrabold ${isOnline ? 'text-primary' : 'text-rose-400 animate-pulse'}`}>
              {isOnline ? 'ONLINE' : 'OFFLINE'}
            </span>
          </div>

          {/* server */}
          <div className="flex flex-col items-center rounded-xl border border-border-default bg-surface p-3">
            <div className="mb-1 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-text-muted">
              <Server
                size={11}
                className={serverConnected ? 'text-primary' : serverConnecting ? 'text-amber-400 animate-pulse' : 'text-rose-400'}
              />
              Server
            </div>
            <span
              className={`text-xs font-extrabold ${
                serverConnected ? 'text-primary' : serverConnecting ? 'text-amber-400 animate-pulse' : 'text-rose-400'
              }`}
            >
              {serverConnected ? 'CONNECTED' : serverConnecting ? 'CONNECTING…' : 'OFFLINE'}
            </span>
          </div>
        </div>

        {/* Retry action */}
        <div className="w-full max-w-xs">
          <motion.button
            {...hoverScale}
            onClick={handleRetry}
            disabled={retrying}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-xs font-extrabold uppercase tracking-wider text-black shadow-lg shadow-primary/20 transition-colors duration-150 hover:bg-primary-hover disabled:opacity-60 cursor-pointer select-none"
          >
            <RefreshCw size={13} className={retrying ? 'animate-spin' : ''} />
            {retrying ? 'Reconnecting…' : 'Retry Connection'}
          </motion.button>
        </div>

        {/* Auto-reconnect note */}
        <p className="mt-4 text-[10px] text-text-muted select-none">
          The terminal reconnects automatically once the feed is restored.
        </p>
      </motion.div>
    </div>
  );
}
