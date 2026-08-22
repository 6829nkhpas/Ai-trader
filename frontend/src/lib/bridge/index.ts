// lib/bridge/index.ts — the single backend-call decision point.
//
// Every backend call in the frontend goes through `bridgeInvoke`, which dispatches
// to the HTTP adapter registry in `webAdapters.ts`.
//
// The point of the indirection is the failure case: a command with no adapter
// throws a typed `BridgeUnsupportedError` naming the command and what is missing,
// instead of an opaque runtime error. That started as a guard against the
// `Cannot read properties of undefined (reading 'invoke')` the Tauri shell
// produced in a browser; it outlived the shell because "this capability has no
// server route" is still a real state worth naming precisely.
//
// The `invoke`-compatible signature is retained deliberately: it costs nothing and
// every call site is already written against it.

import {
  NATIVE_BROWSER_PATH,
  NO_FRONTEND_CALLER,
  NOT_APPLICABLE_ON_WEB,
  PENDING_SERVER_ROUTE,
  WEB_ADAPTERS,
} from './webAdapters';

export {
  bridgeListen,
  emitBridgeEvent,
  hasBridgeListeners,
  relaySse,
  __resetBridgeBus,
  type BridgeEvent,
  type SseFrame,
  type UnlistenFn,
} from './events';
export {
  NATIVE_BROWSER_PATH,
  NO_FRONTEND_CALLER,
  NOT_APPLICABLE_ON_WEB,
  PENDING_SERVER_ROUTE,
  WEB_ADAPTERS,
  type SearchResult,
  type WebAdapter,
} from './webAdapters';

/**
 * Why a command could not run.
 *
 * - `native-browser-path` — the capability exists by another route (e.g. ghost-line
 *   projection is computed in pure TS). Calling through the bridge is the bug.
 * - `desktop-only` — meaningless without a native shell (the updater).
 * - `pending-server-route` — the HTTP equivalent is designed but not deployed yet.
 * - `no-frontend-caller` — deliberately unimplemented because nothing calls it.
 * - `unknown-command` — not a known command at all; almost certainly a typo.
 */
export type BridgeUnsupportedReason =
  | 'native-browser-path'
  | 'desktop-only'
  | 'pending-server-route'
  | 'no-frontend-caller'
  | 'unknown-command';

/**
 * Thrown when a command has no browser implementation.
 *
 * Callers already `catch` and surface `err.message` (see
 * `useQuantStore.sentimentError`), so the message is written to be shown to a
 * user as-is: it says what is unavailable and why, never "undefined".
 */
export class BridgeUnsupportedError extends Error {
  readonly command: string;
  readonly reason: BridgeUnsupportedReason;
  /** The alternative path or planned route, when one is known. */
  readonly detail?: string;

  constructor(command: string, reason: BridgeUnsupportedReason, detail?: string) {
    super(BridgeUnsupportedError.describe(command, reason, detail));
    this.name = 'BridgeUnsupportedError';
    this.command = command;
    this.reason = reason;
    this.detail = detail;
  }

  private static describe(
    command: string,
    reason: BridgeUnsupportedReason,
    detail?: string,
  ): string {
    const suffix = detail ? ` (${detail})` : '';
    switch (reason) {
      case 'native-browser-path':
        return `"${command}" should not be called over the bridge in a browser — a native browser path already covers it${suffix}.`;
      case 'desktop-only':
        return `"${command}" is only available in the desktop app${suffix}.`;
      case 'pending-server-route':
        return `"${command}" is not available on the web yet — the server route is not deployed${suffix}.`;
      case 'no-frontend-caller':
        return `"${command}" has no web implementation because nothing calls it${suffix}.`;
      case 'unknown-command':
        return `"${command}" is not a registered backend command${suffix}.`;
    }
  }
}

function classify(command: string): { reason: BridgeUnsupportedReason; detail?: string } {
  if (command in NATIVE_BROWSER_PATH) {
    return { reason: 'native-browser-path', detail: NATIVE_BROWSER_PATH[command] };
  }
  if (command in NOT_APPLICABLE_ON_WEB) {
    return { reason: 'desktop-only', detail: NOT_APPLICABLE_ON_WEB[command] };
  }
  if (command in PENDING_SERVER_ROUTE) {
    return { reason: 'pending-server-route', detail: PENDING_SERVER_ROUTE[command] };
  }
  if (command in NO_FRONTEND_CALLER) {
    return { reason: 'no-frontend-caller', detail: NO_FRONTEND_CALLER[command] };
  }
  return { reason: 'unknown-command' };
}

/**
 * Call a backend command.
 *
 * Signature-compatible with Tauri's `invoke<T>()` — kept that way because every
 * call site is written against it.
 *
 * @throws {BridgeUnsupportedError} when the command has no adapter. Rejections
 *   from an adapter carry the upstream's own error message, which is what the UI
 *   renders.
 */
export async function bridgeInvoke<T = unknown>(
  command: string,
  args?: Record<string, unknown>,
): Promise<T> {
  const adapter = WEB_ADAPTERS[command];
  if (!adapter) {
    const { reason, detail } = classify(command);
    throw new BridgeUnsupportedError(command, reason, detail);
  }
  return (await adapter(args ?? {})) as T;
}

/**
 * True when `command` has an implementation.
 *
 * For UI that wants to hide a control rather than let it fail.
 */
export function isCommandAvailable(command: string): boolean {
  return command in WEB_ADAPTERS;
}
