'use client';

/**
 * UpdateNotifier — surfaces desktop auto-updates without interrupting trading.
 *
 * The Rust side checks the signed update feed a few seconds after launch and
 * emits `update-available` (see src-tauri/src/commands/updater.rs). This mounts
 * a dismissible card in the corner; downloading and restarting are both explicit
 * user actions.
 *
 * Why it never auto-restarts: this is a live trading terminal. Swapping the
 * binary out from under a user who is holding a position — or mid Deep Quant run
 * — risks real money, so the update is staged and the relaunch waits for them.
 * The download itself is safe to run in the background, but it is still opt-in so
 * a metered connection is never consumed silently.
 *
 * Renders nothing outside Tauri (a browser build has no updater to talk to).
 */

import React, { useCallback, useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Download, RefreshCw, X, ArrowUpCircle } from 'lucide-react';

interface UpdateInfo {
  version: string;
  current_version: string;
  notes?: string | null;
  date?: string | null;
}

interface DownloadProgress {
  downloaded: number;
  total?: number | null;
}

type Phase = 'idle' | 'available' | 'downloading' | 'ready' | 'failed';

const isTauri = () =>
  typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

export default function UpdateNotifier() {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [progress, setProgress] = useState<DownloadProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);

  // ── Subscribe to the Rust-side updater events ──────────────────────────
  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: Array<() => void> = [];
    let cancelled = false;

    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');

        const offAvailable = await listen<UpdateInfo>('update-available', (e) => {
          setInfo(e.payload);
          setPhase('available');
          // A newly published version re-opens the card even if a previous one
          // was dismissed — otherwise a user who dismissed once would never be
          // told about anything again this session.
          setDismissed(false);
        });

        const offProgress = await listen<DownloadProgress>(
          'update-download-progress',
          (e) => setProgress(e.payload),
        );

        const offReady = await listen<string>('update-ready', () => {
          setPhase('ready');
        });

        if (cancelled) {
          offAvailable();
          offProgress();
          offReady();
          return;
        }
        unlisten = [offAvailable, offProgress, offReady];
      } catch (err) {
        // Missing listeners only cost the notification, never the app.
        console.warn('[UpdateNotifier] listener setup failed:', err);
      }
    })();

    return () => {
      cancelled = true;
      for (const off of unlisten) {
        try {
          off();
        } catch {
          /* already torn down */
        }
      }
    };
  }, []);

  const startDownload = useCallback(async () => {
    setPhase('downloading');
    setError(null);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('install_update');
      // `update-ready` normally drives this, but set it here too so the UI is
      // correct even if that event is missed.
      setPhase('ready');
    } catch (err) {
      console.error('[UpdateNotifier] install failed:', err);
      setError(typeof err === 'string' ? err : 'Update failed. Please try again.');
      setPhase('failed');
    }
  }, []);

  const restart = useCallback(async () => {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('relaunch_app');
    } catch (err) {
      console.error('[UpdateNotifier] relaunch failed:', err);
      setError('Could not restart automatically — please reopen Strat Ai.');
    }
  }, []);

  if (!isTauri() || phase === 'idle' || dismissed) return null;

  const pct =
    progress && progress.total && progress.total > 0
      ? Math.min(100, Math.round((progress.downloaded / progress.total) * 100))
      : null;
  const mb = (n: number) => (n / 1_048_576).toFixed(1);

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: 12, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 12, scale: 0.98 }}
        transition={{ duration: 0.18 }}
        role="status"
        aria-live="polite"
        className="fixed bottom-6 right-6 z-[9998] w-80 overflow-hidden rounded-xl border border-border-default bg-elevated shadow-2xl"
      >
        {/* Emerald accent hairline, matching the app's design language. */}
        <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/60 to-transparent" />

        <div className="flex items-start gap-3 p-4">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-border-default bg-surface">
            <ArrowUpCircle size={15} className="text-primary" strokeWidth={2} />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-primary">
                {phase === 'ready'
                  ? 'Update ready'
                  : phase === 'downloading'
                    ? 'Downloading update'
                    : phase === 'failed'
                      ? 'Update failed'
                      : 'Update available'}
              </span>
              {/* Hidden mid-download: cancelling is not supported, so offering an
                  X that only hides the progress would be misleading. */}
              {phase !== 'downloading' && (
                <button
                  type="button"
                  onClick={() => setDismissed(true)}
                  aria-label="Dismiss update notification"
                  className="shrink-0 rounded p-0.5 text-text-muted transition-colors hover:text-text-primary cursor-pointer"
                >
                  <X size={13} />
                </button>
              )}
            </div>

            {phase === 'failed' ? (
              <p className="mt-1 text-xs leading-relaxed text-text-secondary">
                {error ?? 'Something went wrong.'}
              </p>
            ) : phase === 'ready' ? (
              <p className="mt-1 text-xs leading-relaxed text-text-secondary">
                Version {info?.version} is installed. Restart to apply it —
                finish anything in progress first.
              </p>
            ) : phase === 'downloading' ? (
              <div className="mt-2">
                <div className="h-1 w-full overflow-hidden rounded-full bg-surface">
                  <div
                    className="h-full rounded-full bg-primary transition-[width] duration-200"
                    style={{ width: pct !== null ? `${pct}%` : '35%' }}
                  />
                </div>
                <p className="mt-1.5 text-[10px] text-text-muted">
                  {pct !== null
                    ? `${pct}% — ${mb(progress!.downloaded)} MB of ${mb(progress!.total!)} MB`
                    : progress
                      ? `${mb(progress.downloaded)} MB downloaded`
                      : 'Starting…'}
                </p>
              </div>
            ) : (
              <p className="mt-1 text-xs leading-relaxed text-text-secondary">
                Strat Ai {info?.version} is available
                {info?.current_version ? ` (you have ${info.current_version})` : ''}.
              </p>
            )}

            {/* Actions */}
            {(phase === 'available' || phase === 'failed') && (
              <button
                type="button"
                onClick={startDownload}
                className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-[11px] font-extrabold uppercase tracking-wider text-black shadow-lg shadow-primary/20 transition-colors hover:bg-primary-hover cursor-pointer"
              >
                <Download size={12} />
                {phase === 'failed' ? 'Retry' : 'Download & install'}
              </button>
            )}
            {phase === 'ready' && (
              <button
                type="button"
                onClick={restart}
                className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-3 py-2 text-[11px] font-extrabold uppercase tracking-wider text-black shadow-lg shadow-primary/20 transition-colors hover:bg-primary-hover cursor-pointer"
              >
                <RefreshCw size={12} />
                Restart now
              </button>
            )}
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
