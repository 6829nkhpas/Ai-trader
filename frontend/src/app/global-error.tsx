'use client';

/**
 * Last-resort boundary for errors thrown in the root layout itself, where
 * `app/error.tsx` cannot mount. It must render its own <html>/<body> because it
 * REPLACES the root layout rather than nesting inside it.
 *
 * Deliberately styled with inline styles and no imports beyond React: whatever
 * broke may well be the layout's font/CSS pipeline, so this path must not
 * depend on it.
 */

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '16px',
          background: '#0a0a0a',
          color: '#e5e5e5',
          fontFamily: 'system-ui, -apple-system, sans-serif',
          textAlign: 'center',
          padding: '24px',
        }}
      >
        <h2 style={{ fontSize: '16px', fontWeight: 800, margin: 0 }}>
          The terminal failed to start
        </h2>
        <p style={{ fontSize: '12px', color: '#a3a3a3', maxWidth: '28rem', margin: 0, lineHeight: 1.6 }}>
          An error occurred while loading the application shell.
        </p>
        {error.message && (
          <p
            style={{
              fontSize: '10px',
              fontFamily: 'ui-monospace, monospace',
              color: '#a3a3a3',
              background: '#171717',
              border: '1px solid #262626',
              borderRadius: '4px',
              padding: '6px 8px',
              maxWidth: '28rem',
              wordBreak: 'break-word',
              margin: 0,
            }}
          >
            {error.message}
          </p>
        )}
        <button
          type="button"
          onClick={reset}
          style={{
            background: '#10b981',
            color: '#000',
            border: 'none',
            borderRadius: '8px',
            padding: '10px 20px',
            fontSize: '12px',
            fontWeight: 800,
            textTransform: 'uppercase',
            letterSpacing: '0.05em',
            cursor: 'pointer',
          }}
        >
          Retry
        </button>
      </body>
    </html>
  );
}
