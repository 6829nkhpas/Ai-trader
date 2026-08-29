'use client';

/**
 * Notification bell + dropdown.
 *
 * The bell used to be a dead button: no `onClick`, no handler, no accessible
 * name, and a red "unread" dot that was hardcoded on permanently — it signalled
 * unread notifications that did not exist and could not be opened. That is the
 * "Notification Icon — not working" report.
 *
 * The feed is REAL: `useTradeStore.systemLogs`, the same WARN/ERROR/INFO stream
 * the terminal's system console renders, written by the WebSocket bootstrap,
 * the paper-trade engine and the agent pipeline. Nothing is synthesised here —
 * with no logs the panel says so rather than inventing entries.
 *
 * "Unread" is tracked against the timestamp of the newest log the user has seen,
 * held in component state: it is per-session by design, since the log itself is
 * per-session (capped at 500 in the store, not persisted).
 */

import React, { useCallback, useMemo, useState } from 'react';
import { Bell, AlertTriangle, Info, XCircle, CheckCheck } from 'lucide-react';
import { useTradeStore, type SystemLog } from '../../store/useTradeStore';
import { useOutsideClose } from '../../hooks/useOutsideClose';

/** Only actionable levels count toward the unread badge. */
const NOTIFY_LEVELS: SystemLog['level'][] = ['WARN', 'ERROR'];

function levelIcon(level: SystemLog['level']) {
  if (level === 'ERROR') return <XCircle size={10} className="shrink-0 text-rose-500 dark:text-rose-400" />;
  if (level === 'WARN') return <AlertTriangle size={10} className="shrink-0 text-amber-500 dark:text-amber-400" />;
  return <Info size={10} className="shrink-0 text-text-muted" />;
}

function levelTone(level: SystemLog['level']): string {
  if (level === 'ERROR') return 'text-rose-600 dark:text-rose-400';
  if (level === 'WARN') return 'text-amber-600 dark:text-amber-400';
  return 'text-text-secondary';
}

function timeAgo(ts: number): string {
  const diff = Math.max(0, Date.now() - ts);
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

export default function NotificationBell() {
  const systemLogs = useTradeStore((s) => s.systemLogs);
  const [isOpen, setIsOpen] = useState(false);
  const [seenUpTo, setSeenUpTo] = useState(0);

  const close = useCallback(() => setIsOpen(false), []);
  const containerRef = useOutsideClose<HTMLDivElement>(close);

  // Newest first, actionable levels only. The console panel remains the place to
  // read the full INFO-level trace.
  const notifications = useMemo(
    () =>
      systemLogs
        .filter((l) => NOTIFY_LEVELS.includes(l.level))
        .slice(-50)
        .reverse(),
    [systemLogs],
  );

  const unreadCount = useMemo(
    () => notifications.filter((n) => n.timestamp > seenUpTo).length,
    [notifications, seenUpTo],
  );

  const markAllRead = () => {
    setSeenUpTo(notifications.length > 0 ? notifications[0].timestamp : Date.now());
  };

  const handleToggle = () => {
    const next = !isOpen;
    setIsOpen(next);
    // Opening the panel is what marks things read.
    if (next) markAllRead();
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        onClick={handleToggle}
        aria-label={
          unreadCount > 0
            ? `Notifications, ${unreadCount} unread`
            : 'Notifications'
        }
        aria-expanded={isOpen}
        title={unreadCount > 0 ? `${unreadCount} unread notification${unreadCount === 1 ? '' : 's'}` : 'Notifications'}
        className={`relative rounded p-1 transition-colors hover:bg-elevated/20 ${
          isOpen ? 'text-text-primary bg-elevated/20' : 'text-text-secondary hover:text-text-primary'
        }`}
      >
        <Bell size={15} />
        {/* Shown only when there is genuinely something unread. */}
        {unreadCount > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-3 min-w-3 items-center justify-center rounded-full bg-red-500 px-0.5 text-[8px] font-black leading-none text-white tabular-nums">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full z-[999] mt-2 flex w-80 flex-col rounded-none border border-border-default bg-surface/95 shadow-2xl backdrop-blur-xl">
          <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
            <div className="flex items-center gap-2">
              <Bell size={13} className="text-text-secondary" />
              <span className="text-xs font-bold uppercase tracking-wide text-text-primary">
                Notifications
              </span>
              {notifications.length > 0 && (
                <span className="rounded-full bg-elevated px-1.5 py-0.5 text-[9px] font-bold tabular-nums text-text-secondary">
                  {notifications.length}
                </span>
              )}
            </div>
            {notifications.length > 0 && (
              <button
                type="button"
                onClick={markAllRead}
                className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
              >
                <CheckCheck size={9} />
                Mark read
              </button>
            )}
          </div>

          <div className="max-h-80 overflow-y-auto scrollbar-thin">
            {notifications.length === 0 ? (
              <div className="flex flex-col items-center gap-1 px-3 py-6 text-center">
                <Bell size={14} className="text-text-muted/50" />
                <p className="text-[11px] font-bold text-text-secondary">Nothing to report</p>
                <p className="text-[9px] leading-relaxed text-text-muted/70">
                  Feed warnings and errors from this session appear here.
                </p>
              </div>
            ) : (
              notifications.map((n) => (
                <div
                  key={`${n.timestamp}-${n.message}`}
                  className="flex items-start gap-2 border-b border-border-default/40 px-3 py-2 last:border-b-0 hover:bg-elevated/20"
                >
                  <span className="mt-0.5">{levelIcon(n.level)}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className={`text-[9px] font-black uppercase tracking-wider ${levelTone(n.level)}`}>
                        {n.level}
                      </span>
                      <span className="shrink-0 text-[9px] text-text-muted/70 tabular-nums">
                        {timeAgo(n.timestamp)}
                      </span>
                    </div>
                    <p className="mt-0.5 break-words text-[10px] leading-normal text-text-secondary">
                      {n.message}
                    </p>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
