import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  // A self-contained Node server: the `/api/*` proxy handlers need a server, and
  // `standalone` traces only the modules it actually uses so the runtime Docker
  // image stays lean.
  //
  // This used to switch to `output: 'export'` under NEXT_OUTPUT_EXPORT for the
  // Tauri desktop bundle. That whole branch is gone with the desktop shell, and
  // with it `trailingSlash` (which 308-redirected every `/api/*` call, breaking
  // streaming POSTs whose ReadableStream body cannot be replayed) and the custom
  // `pageExtensions` that admitted `route.web.ts` only outside export mode. Those
  // handlers are now plain `route.ts`.
  output: 'standalone',

  images: { unoptimized: true },

  // Don't fail the production build on ESLint style errors (e.g. the pre-existing
  // `no-explicit-any` findings across the codebase). Linting is still run
  // separately via `npm run lint`; blocking the build on style rules is not
  // appropriate. TypeScript type-checking stays ON (real type errors still fail).
  eslint: { ignoreDuringBuilds: true },

  // Pin the Turbopack workspace root to the frontend folder so Next.js does
  // not guess between the two lockfiles in the monorepo (root + frontend/).
  // Silences the "inferred workspace root" warning during dev/build.
  //
  // `build:web` passes `--turbopack`, and that is load-bearing rather than a speed
  // preference: the default webpack production build dies partway through
  // compilation with
  //
  //   FATAL ERROR: Committing semi space failed. Allocation failed -
  //   JavaScript heap out of memory
  //
  // crashing in ArrayBuffer/Buffer allocation with the JS heap flat at ~167 MB.
  // Because the exhaustion is in EXTERNAL memory, not the JS heap, raising
  // --max-old-space-size (tried at 8 GB) does not help. Turbopack compiles the
  // same cold tree without the spike. If you ever drop the flag, verify a COLD
  // build (delete .next first) — a warm cache can mask this.
  turbopack: {
    root: path.resolve(__dirname),
  },

  // Both prefixes resolve to the same-origin handlers in `src/app/api/`, which
  // hold the gateway basic-auth credential server-side (see `app/api/_gateway.ts`).
  // They used to point straight at `http://127.0.0.1:8087` and
  // `http://127.0.0.1:9000`, which is why the browser reported `Failed to fetch`
  // at `kiteFetch` on any machine not also running the local aggregator.
  async rewrites() {
    return [
      {
        source: '/questdb/:path*',
        destination: '/api/questdb/:path*',
      },
      {
        source: '/kite/:path*',
        destination: '/api/kite/:path*',
      },
    ];
  },
};

export default nextConfig;
