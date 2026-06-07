import { NextConfig } from 'next';

// Static export — the business portal is a pure browser SPA served as static
// assets (no Node server, no Tauri shell). All data is fetched client-side
// from the M1 API. Mirrors the accountant-portal build.
const nextConfig: NextConfig = {
  output: 'export',
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
