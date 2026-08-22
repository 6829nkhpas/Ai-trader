// lib/bridge/events.ts — the app's event surface.
//
// Backend pushes reach the browser two ways, and neither is IPC:
//   * live market data over `/ws/*`, the one gateway prefix with no basic auth
//     (`infra/caddy/Caddyfile`), connected directly by
//     `useTradeStore.connectAlphaWebSocket` and friends;
//   * agent/analysis frames over same-origin SSE, relayed onto the local bus by
//     `relaySse` below.
//
// `bridgeListen` is the subscribe side of that bus. It keeps Tauri's `listen<T>()`
// signature and envelope shape — the desktop shell is gone, but every call site
// was written against that shape and there is nothing to gain from churning them.

/** An unsubscribe handle. */
export type UnlistenFn = () => void;

/** The event envelope handed to every listener. */
export interface BridgeEvent<T> {
  event: string;
  payload: T;
  /** Always 0 — kept so the envelope shape is stable for existing callers. */
  id: number;
}

type Handler = (event: BridgeEvent<unknown>) => void;

/** name → live handlers. Module-scoped so adapters and listeners share it. */
const handlers = new Map<string, Set<Handler>>();

/** Publish onto the bus. */
export function emitBridgeEvent<T>(name: string, payload: T): void {
  const set = handlers.get(name);
  if (!set || set.size === 0) return;
  const envelope: BridgeEvent<unknown> = { event: name, payload, id: 0 };
  // Snapshot before iterating: a handler may unsubscribe itself mid-dispatch.
  for (const h of [...set]) {
    try {
      h(envelope);
    } catch (err) {
      console.error(`[bridge] listener for "${name}" threw:`, err);
    }
  }
}

/** True when at least one component is listening — lets adapters skip work. */
export function hasBridgeListeners(name: string): boolean {
  return (handlers.get(name)?.size ?? 0) > 0;
}

/**
 * Subscribe to a backend event.
 *
 * Async, and returning a promise of the unsubscribe fn, because that is the
 * signature every call site already awaits.
 */
export async function bridgeListen<T>(
  name: string,
  cb: (event: BridgeEvent<T>) => void,
): Promise<UnlistenFn> {
  const handler = cb as Handler;
  let set = handlers.get(name);
  if (!set) {
    set = new Set();
    handlers.set(name, set);
  }
  set.add(handler);

  return () => {
    const current = handlers.get(name);
    if (!current) return;
    current.delete(handler);
    if (current.size === 0) handlers.delete(name);
  };
}

/** Drop every bus subscription. Test-only. */
export function __resetBridgeBus(): void {
  handlers.clear();
}

// ── SSE → bus relay ─────────────────────────────────────────────────────────
//
// The deep-quant service speaks Server-Sent Events, consumed here over the
// same-origin `/api/deepquant/*` proxy and re-emitted as `{ event, data }` frames.

/** One parsed SSE block. */
export interface SseFrame {
  event: string;
  data: unknown;
}

/** Line terminators the SSE spec allows: CRLF, CR, or LF. */
const SSE_LINE_END = /\r\n|\r|\n/g;

/**
 * Split an SSE byte stream into frames and hand each to `onFrame`.
 *
 * Frame semantics:
 *
 * - ALL `data:` lines in a block are accumulated and joined with `\n` before
 *   parsing — the Python side emits multi-line JSON for large glass-box payloads.
 * - Data that will not parse yields `null` rather than dropping the frame, so a
 *   malformed payload cannot silently shorten a transcript.
 * - A block with no `event:` line is ignored (keep-alive comments, and the
 *   default `message` type the agent never emits).
 *
 * Line splitting is done per the SSE spec rather than on a literal `"\n\n"`, so a
 * proxy that normalises line endings to CRLF cannot silently wedge the stream —
 * the byte sequence `\r\n\r\n` contains no `\n\n`. Per spec, a trailing
 * incomplete line at end-of-stream is discarded.
 */
export async function relaySse(
  body: ReadableStream<Uint8Array>,
  onFrame: (frame: SseFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  let eventType: string | null = null;
  let dataLines: string[] = [];

  const dispatch = () => {
    if (eventType) {
      let data: unknown = null;
      if (dataLines.length > 0) {
        try {
          data = JSON.parse(dataLines.join('\n'));
        } catch {
          data = null;
        }
      }
      onFrame({ event: eventType, data });
    }
    eventType = null;
    dataLines = [];
  };

  const handleLine = (line: string) => {
    if (line === '') {
      dispatch();
      return;
    }
    if (line.startsWith(':')) return; // comment / keep-alive
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    // The spec strips exactly one leading space from the value.
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') eventType = value.trim();
    else if (field === 'data') dataLines.push(value.trim());
  };

  const onAbort = () => {
    void reader.cancel().catch(() => {});
  };
  signal?.addEventListener('abort', onAbort, { once: true });

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // A trailing lone CR is ambiguous — the LF of a CRLF pair may be in the
      // next chunk — so hold it back rather than treating it as a terminator.
      let searchable = buffer;
      let carry = '';
      if (searchable.endsWith('\r')) {
        carry = '\r';
        searchable = searchable.slice(0, -1);
      }

      SSE_LINE_END.lastIndex = 0;
      let consumed = 0;
      let match: RegExpExecArray | null;
      while ((match = SSE_LINE_END.exec(searchable)) !== null) {
        handleLine(searchable.slice(consumed, match.index));
        consumed = match.index + match[0].length;
      }
      buffer = searchable.slice(consumed) + carry;
    }
  } finally {
    signal?.removeEventListener('abort', onAbort);
    reader.releaseLock();
  }
}
