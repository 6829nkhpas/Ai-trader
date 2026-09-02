// app/find-trade/session/[sessionId]/not-found.tsx
//
// Reached for a session that does not exist, is deleted, or belongs to someone else.
//
// All three say the same thing on purpose. Distinguishing "not yours" from "does not exist" would
// confirm that an id is real, turning the route into an enumeration oracle — the API answers 404 for
// both (see the design record), and this page must not undo that by being more specific.

import Link from 'next/link';

export default function SessionNotFound() {
  return (
    <main className="flex h-dvh items-center justify-center bg-surface p-6">
      <div className="max-w-sm text-center">
        <h1 className="text-sm font-medium text-text-primary">This session isn’t available</h1>
        {/* No speculation about why. Guessing "it may have been deleted" for a session that is simply
            someone else's is both wrong and a hint. */}
        <p className="mt-2 text-xs text-text-secondary">
          It may have been deleted, or the link may be incorrect.
        </p>
        <Link
          href="/"
          className="mt-4 inline-block rounded border border-border-default/60 px-3 py-1 text-xs text-text-secondary hover:bg-elevated hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
        >
          Back to the terminal
        </Link>
      </div>
    </main>
  );
}
