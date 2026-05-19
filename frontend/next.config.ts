import type { NextConfig } from "next";
import path from "node:path";

const isTestMode = process.env.ALPHA_TEST_MODE === '1' || process.env.ALPHA_TEST_MODE === 'true';

const nextConfig: NextConfig = {
  // output: 'export', // Disabled because we use Next.js rewrites for the API in dev mode
  trailingSlash: true,
  images: { unoptimized: true },

  // Pin the Turbopack workspace root to the frontend folder so Next.js does
  // not guess between the two lockfiles in the monorepo (root + frontend/).
  // Silences the "inferred workspace root" warning during dev/build.
  turbopack: {
    root: path.resolve(__dirname),
  },

  async rewrites() {
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
        destination: 'http://127.0.0.1:8084/api/kite/:path*',
      },
    ];
  },
};

export default nextConfig;
