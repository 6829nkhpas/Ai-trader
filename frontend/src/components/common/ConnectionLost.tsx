'use client';

import React, { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { Wifi, WifiOff, Server, RefreshCw, AlertTriangle } from 'lucide-react';
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

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-background/95 backdrop-blur-md select-none transition-all duration-300">
      {/* Background gradients */}
      <div className="absolute top-1/4 left-1/4 -translate-x-1/2 -translate-y-1/2 w-96 h-96 rounded-full bg-emerald-500/5 blur-[120px] pointer-events-none" />
      <div className="absolute bottom-1/4 right-1/4 translate-x-1/2 translate-y-1/2 w-96 h-96 rounded-full bg-[#6c63ff]/5 blur-[120px] pointer-events-none" />

      {/* Main glass card */}
      <motion.div
        variants={fadeInUp}
        initial="hidden"
        animate="show"
        className="relative overflow-hidden w-full max-w-xl rounded-2xl border border-border-default/80 bg-card/65 p-8 md:p-10 shadow-2xl flex flex-col items-center text-center"
      >
        {/* SVG Illustration */}
        <div className="w-full max-w-[340px] h-auto mb-8 relative">
          <img
            src="/connection-lost.svg"
            alt="Connection Lost"
            className="w-full h-auto object-contain drop-shadow-md select-none"
          />
        </div>

        {/* Title */}
        <h2 className="text-xl md:text-2xl font-black text-text-primary tracking-tight mb-2">
          Connectivity Interrupted
        </h2>
        <p className="text-xs text-text-secondary max-w-md mb-6 leading-relaxed">
          We detected a disruption in your terminal feed. Please verify your internet connection or check if the local trading server instance is running.
        </p>

        {/* Status Indicators List */}
        <div className="w-full max-w-xs grid grid-cols-2 gap-3 mb-8">
          {/* Internet Connectivity Status */}
          <div className="flex flex-col items-center p-3 rounded-xl border border-border-default/60 bg-card">
            <div className="flex items-center gap-1.5 mb-1 text-[10px] text-text-muted font-bold uppercase tracking-wider">
              {isOnline ? <Wifi size={11} className="text-emerald-600 dark:text-emerald-400" /> : <WifiOff size={11} className="text-rose-600 dark:text-rose-400" />}
              Internet
            </div>
            <span className={`text-xs font-extrabold ${isOnline ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400 animate-pulse'}`}>
              {isOnline ? 'ONLINE' : 'OFFLINE'}
            </span>
          </div>

          {/* Server Connection Status */}
          <div className="flex flex-col items-center p-3 rounded-xl border border-border-default/60 bg-card">
            <div className="flex items-center gap-1.5 mb-1 text-[10px] text-text-muted font-bold uppercase tracking-wider">
              <Server size={11} className={wsStatus === 'connected' ? 'text-emerald-600 dark:text-emerald-400' : wsStatus === 'connecting' ? 'text-amber-600 dark:text-amber-400 animate-pulse' : 'text-rose-600 dark:text-rose-400'} />
              Trading Server
            </div>
            <span className={`text-xs font-extrabold ${
              wsStatus === 'connected' ? 'text-emerald-600 dark:text-emerald-400' :
              wsStatus === 'connecting' ? 'text-amber-600 dark:text-amber-400 animate-pulse' :
              'text-rose-600 dark:text-rose-400'
            }`}>
              {wsStatus === 'connected' ? 'CONNECTED' :
               wsStatus === 'connecting' ? 'CONNECTING...' :
               'OFFLINE'}
            </span>
          </div>
        </div>

        {/* Retry Actions */}
        <div className="flex flex-col sm:flex-row items-center gap-3 w-full max-w-xs">
          <motion.button
            variants={hoverScale}
            whileHover="hover"
            whileTap="tap"
            onClick={handleRetry}
            disabled={retrying}
            className="w-full flex items-center justify-center gap-2 rounded-lg bg-[#6c63ff] hover:bg-[#5b52e0] disabled:bg-[#6c63ff]/60 px-5 py-2.5 text-xs font-extrabold uppercase tracking-wider text-white shadow-lg cursor-pointer select-none transition-colors duration-150"
          >
            <RefreshCw size={13} className={retrying ? 'animate-spin' : ''} />
            {retrying ? 'Reconnecting...' : 'Retry Connection'}
          </motion.button>
        </div>

        {/* Auto-reconnect note */}
        <p className="text-[10px] text-text-muted mt-4 select-none">
          App automatically recovers once connection is restored
        </p>
      </motion.div>
    </div>
  );
}
