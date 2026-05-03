import type { Metadata } from 'next';
import React from 'react';
import { Inter, Outfit } from 'next/font/google';

export const metadata: Metadata = {
  title: 'Trivx — Identity Gateway',
  description: 'Sign in or create your Trivx AI-Trade account.',
};

const outfit = Outfit({
  variable: '--font-outfit',
  subsets: ['latin'],
  display: 'swap',
});

const inter = Inter({
  variable: '--font-inter',
  subsets: ['latin'],
  display: 'swap',
});

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div
      className={`${outfit.variable} ${inter.variable} auth-shell`}
      suppressHydrationWarning
    >
      {/* ── Ambient glow orbs ─────────────────────────────────────────── */}
      <div aria-hidden="true" className="auth-orb auth-orb--1" />
      <div aria-hidden="true" className="auth-orb auth-orb--2" />
      <div aria-hidden="true" className="auth-orb auth-orb--3" />

      {/* ── Subtle grid overlay ───────────────────────────────────────── */}
      <div aria-hidden="true" className="auth-grid-overlay" />

      {/* ── Card ─────────────────────────────────────────────────────── */}
      <main className="auth-card-wrapper">
        {/* Logo / wordmark */}
        <div className="auth-logo-row">
          <div className="auth-logo-icon" aria-hidden="true">
            <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
              <path
                d="M14 2L26 8V20L14 26L2 20V8L14 2Z"
                stroke="url(#trivxGrad)"
                strokeWidth="1.5"
                fill="none"
              />
              <path
                d="M14 7L21 11V18L14 22L7 18V11L14 7Z"
                fill="url(#trivxGrad)"
                opacity="0.25"
              />
              <circle cx="14" cy="14" r="3" fill="url(#trivxGrad)" />
              <defs>
                <linearGradient id="trivxGrad" x1="2" y1="2" x2="26" y2="26">
                  <stop offset="0%" stopColor="#818cf8" />
                  <stop offset="100%" stopColor="#06b6d4" />
                </linearGradient>
              </defs>
            </svg>
          </div>
          <span className="auth-logo-text">Trivx</span>
        </div>

        {/* Form card */}
        <div className="auth-card">
          {children}
        </div>

        {/* Footer */}
        <p className="auth-footer">
          © {new Date().getFullYear()} Trivx Technologies. All rights reserved.
          &nbsp;·&nbsp;
          <a href="#" className="auth-link">Privacy</a>
          &nbsp;·&nbsp;
          <a href="#" className="auth-link">Terms</a>
        </p>
      </main>
    </div>
  );
}
