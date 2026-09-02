// app/find-trade/session/[sessionId]/loading.tsx
//
// Shown while the route segment resolves. Deliberately the SHAPE of the workspace — tab strip, header,
// transcript, composer — rather than a centred spinner, so the layout does not jump when the real
// content arrives.

export default function Loading() {
  return (
    <main className="flex h-dvh flex-col bg-surface" role="status" aria-live="polite">
      <span className="sr-only">Loading session…</span>
      <div className="flex shrink-0 gap-1 border-b border-border-default/40 px-2 py-2" aria-hidden="true">
        <div className="h-6 w-32 animate-pulse rounded bg-elevated" />
        <div className="h-6 w-32 animate-pulse rounded bg-elevated" />
      </div>
      <div className="shrink-0 border-b border-border-default/40 px-3 py-2" aria-hidden="true">
        <div className="h-4 w-48 animate-pulse rounded bg-elevated" />
      </div>
      <div className="flex-1 space-y-3 p-3" aria-hidden="true">
        <div className="h-4 w-3/4 animate-pulse rounded bg-elevated" />
        <div className="h-4 w-2/3 animate-pulse rounded bg-elevated" />
        <div className="h-4 w-5/6 animate-pulse rounded bg-elevated" />
      </div>
      <div className="shrink-0 border-t border-border-default/40 p-3" aria-hidden="true">
        <div className="h-12 animate-pulse rounded bg-elevated" />
      </div>
    </main>
  );
}
