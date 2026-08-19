import type { NextConfig } from "next";
import path from "node:path";

const isTestMode = process.env.ALPHA_TEST_MODE === '1' || process.env.ALPHA_TEST_MODE === 'true';

// Static export is required for the Tauri desktop production bundle (frontendDist
// points at ../out). It is toggled on ONLY when NEXT_OUTPUT_EXPORT=1 so that dev
// mode (next dev + rewrites) and the hosted web build are completely unaffected.
const staticExport = process.env.NEXT_OUTPUT_EXPORT === '1' || process.env.NEXT_OUTPUT_EXPORT === 'true';

const nextConfig: NextConfig = {
  // Emit a fully static site into ../out for the Tauri bundle when exporting.
  ...(staticExport ? { output: 'export' as const } : {}),
  trailingSlash: true,
  images: { unoptimized: true },

  // Don't fail the production build on ESLint style errors (e.g. the pre-existing
  // `no-explicit-any` findings across the codebase). Linting is still run
  // separately via `npm run lint`; blocking the installer build on style rules is
  // not appropriate. TypeScript type-checking stays ON (real type errors still
  // fail the build).
  eslint: { ignoreDuringBuilds: true },

  // Pin the Turbopack workspace root to the frontend folder so Next.js does
  // not guess between the two lockfiles in the monorepo (root + frontend/).
  // Silences the "inferred workspace root" warning during dev/build.
  //
  // `build:desktop` also passes `--turbopack`, and that is load-bearing rather
  // than a speed preference: on Node 24 the default webpack production build
  // dies partway through compilation with
  //
  //   FATAL ERROR: Committing semi space failed. Allocation failed -
  //   JavaScript heap out of memory
  //
  // crashing in ArrayBuffer/Buffer allocation with the JS heap flat at ~167 MB.
  // Because the exhaustion is in EXTERNAL memory, not the JS heap, raising
  // --max-old-space-size (tried at 8 GB) does not help. Turbopack compiles the
  // same cold tree to a complete ../out without the spike. If you ever drop the
  // flag, verify a COLD build (delete .next first) — a warm cache can mask this.
  turbopack: {
    root: path.resolve(__dirname),
  },

  // Rewrites are only used in dev / hosted-web mode. Static export (Tauri
  // production bundle) has no Node server — the Rust core proxies over IPC.
  // Omitting the key entirely in export mode silences the Next.js warning.
  ...(!staticExport
    ? {
        async rewrites() {
          if (isTestMode) {
            return [
              {
                source: '/kite/:path*',
                destination: '/api/kite/:path*',
              },
            ];
          }

          return [
            {
              source: '/questdb/:path*',
              destination: 'http://127.0.0.1:9000/:path*',
            },
            {
              source: '/kite/:path*',
              destination: 'http://127.0.0.1:8087/api/kite/:path*',
            },
          ];
        },
      }
    : {}),
};

export default nextConfig;
