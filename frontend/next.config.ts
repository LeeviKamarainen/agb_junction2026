import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // Browser code calls relative /api URLs. In production Caddy sends /api to FastAPI before
  // requests reach Next.js; this rewrite does the same job under `next dev`.
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL;
    if (!backendUrl) throw new Error("BACKEND_URL must be set, e.g. http://localhost:8000");
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
