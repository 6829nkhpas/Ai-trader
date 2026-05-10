import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // output: 'export', // Disabled because we use Next.js rewrites for the API in dev mode
  trailingSlash: true,
  images: { unoptimized: true },
  async rewrites() {
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
