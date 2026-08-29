/**
 * Gated verbose logging for the hot paths.
 *
 * The chart redraw, datafeed and quant-store paths carried ~45 unconditional
 * `console.log` calls between them. Each one is useful when you are debugging
 * that specific subsystem, and together they made the console unreadable in
 * normal use — a ghost-line redraw alone printed the resolved interval, the
 * projection length, the engine mode, the point count and the full list of
 * projected times and prices, on every new bar, every zoom and every pulse. The
 * reported "console me bhout error aa rha h" is mostly this: real warnings were
 * buried in routine chatter.
 *
 * `console.warn` / `console.error` are deliberately NOT routed through here —
 * those report something actually going wrong and should always be visible.
 *
 * Enabling it, without a rebuild:
 *   · set `NEXT_PUBLIC_DEBUG_LOGS=true` at build time, or
 *   · run `localStorage.setItem('stratai.debug', 'true')` in the console and
 *     reload (works on a deployed build, which is the point).
 */

const ENV_ENABLED = process.env.NEXT_PUBLIC_DEBUG_LOGS === 'true';

/** Read once at module load: this is a per-session switch, not a hot path check. */
function readStorageFlag(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return localStorage.getItem('stratai.debug') === 'true';
  } catch {
    return false; // private mode / storage disabled
  }
}

export const DEBUG_LOGS_ENABLED = ENV_ENABLED || readStorageFlag();

/**
 * Verbose log, emitted only when debug logging is enabled.
 *
 * Signature-compatible with `console.log`, so converting a call site is just a
 * rename.
 */
export function debugLog(...args: unknown[]): void {
  if (!DEBUG_LOGS_ENABLED) return;
  console.log(...args);
}
