'use client';

import React, { useState } from 'react';
import { useAuthStore } from '../../store/useAuthStore';
import { Loader2 } from 'lucide-react';
import Antigravity from '../Antigravity';

export default function AuthOverlay() {
  const login = useAuthStore((s) => s.login);
  const [loading, setLoading] = useState(false);

  const handleLogin = async () => {
    if (loading) return;
    setLoading(true);
    try {
      await login();
    } catch {
      // Silently handle — login always succeeds for now
    } finally {
      setLoading(false);
    }
  };

  const handleContactClick = async (e: React.MouseEvent) => {
    e.preventDefault();
    const url = 'https://www.stratai.live/contact';
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('open_browser', { url });
    } catch {
      if (typeof window !== 'undefined') {
        window.open(url, '_blank', 'noopener,noreferrer');
      }
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#0a0e17] overflow-hidden">
      {/* Antigravity Background */}
      <div className="absolute inset-0 z-0">
        <Antigravity
          count={5000}
          magnetRadius={5}
          ringRadius={3}
          waveSpeed={0.7}
          waveAmplitude={1}
          particleSize={0.7}
          lerpSpeed={0.15}
          color="#b9ff9f"
          autoAnimate
          particleVariance={0.8}
          rotationSpeed={0.2}
          depthFactor={0.5}
          pulseSpeed={4}
          particleShape="sphere"
          fieldStrength={10}
        />
      </div>

      {/* Subtle radial glow behind the card */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
        <div
          className="absolute left-1/2 top-[38%] -translate-x-1/2 -translate-y-1/2 h-[420px] w-[420px] rounded-full opacity-[0.07]"
          style={{
            background:
              'radial-gradient(circle, rgba(16,185,129,0.8) 0%, rgba(56,189,248,0.4) 40%, transparent 70%)',
          }}
        />
      </div>

      {/* Card */}
      <div className="relative flex flex-col items-center gap-6 px-8 py-10 w-full max-w-sm z-10">
        {/* Logo */}
        <div className="relative">
          <img
            src="/strat.svg"
            alt="Strat AI"
            className="h-16 w-16 object-contain drop-shadow-lg"
          />
        </div>

        {/* Title */}
        <h1 className="text-xl font-semibold tracking-tight text-white/90 text-center">
          Welcome to Strat AI
        </h1>

        {/* Login Button */}
        <button
          type="button"
          onClick={handleLogin}
          disabled={loading}
          className="flex items-center justify-center gap-2.5 rounded-lg border border-[#1e2a3a] bg-[#131922] hover:bg-[#1a2332] px-6 py-3 text-sm font-semibold text-white/90 transition-all duration-200 cursor-pointer disabled:opacity-50 disabled:cursor-wait shadow-lg shadow-black/20 hover:border-[#2a3a4e] hover:shadow-xl hover:shadow-black/30 active:scale-[0.98] min-w-[220px]"
        >
          {loading ? (
            <>
              <Loader2 size={16} className="animate-spin" />
              <span>Connecting...</span>
            </>
          ) : (
            <>
              {/* Strat AI mini icon */}
              <img
                src="/strat.svg"
                alt=""
                className="h-4 w-4 object-contain"
              />
              <span>Login with Strat AI</span>
            </>
          )}
        </button>

        {/* Subtle helper link */}
        <p className="text-xs text-[#475569] text-center">
          Having trouble?{' '}
          <a
            href="https://www.stratai.live/contact"
            onClick={handleContactClick}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[#64748b] underline underline-offset-2 hover:text-[#94a3b8] transition-colors cursor-pointer"
          >
            Let us know
          </a>
        </p>
      </div>
    </div>
  );
}
