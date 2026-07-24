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

  // Pin the Turbopack workspace root to the frontend folder so Next.js does
  // not guess between the two lockfiles in the monorepo (root + frontend/).
  // Silences the "inferred workspace root" warning during dev/build.
  turbopack: {
    root: path.resolve(__dirname),
  },

  async rewrites() {
    // Static export has no Node server, so rewrites are unsupported (and
    // unnecessary — the native Tauri Rust core proxies QuestDB/Kite over IPC).
    if (staticExport) {
      return [];
    }

    // In test mode: route /kite/* → local Next.js mock API routes so that
    // useHistoricalData can fetch synthetic candles for any symbol without
    // needing the real aggregator running. /questdb/* returns 503 (no mock).
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
};

export default nextConfig;
