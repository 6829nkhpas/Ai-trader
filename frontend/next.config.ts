import type { NextConfig } from "next";

const isTestMode = process.env.ALPHA_TEST_MODE === '1' || process.env.ALPHA_TEST_MODE === 'true';

const nextConfig: NextConfig = {
  // output: 'export', // Disabled because we use Next.js rewrites for the API in dev mode
  trailingSlash: true,
  images: { unoptimized: true },
  async rewrites() {
    // In test mode, don't proxy to external services — use local API route handlers
    if (isTestMode) {
      return [];
    }

    return [
      {
        source: '/api/:path*',
        destination: 'http://127.0.0.1:3001/api/:path*',
      },
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
