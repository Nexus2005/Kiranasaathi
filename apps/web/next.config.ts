import type { NextConfig } from "next";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      // Public storefront API proxy — keeps the FastAPI origin out of
      // customer-facing pages. The merchant app calls the API directly.
      { source: "/storefront/api/:slug", destination: `${API}/public/store/:slug` },
      { source: "/storefront/api/:slug/products/:pid", destination: `${API}/public/store/:slug/products/:pid` },
      { source: "/storefront/api/:slug/checkout", destination: `${API}/public/store/:slug/checkout` },
      { source: "/storefront/api/:slug/orders/:token", destination: `${API}/public/store/:slug/orders/:token` },
      { source: "/storefront/api/:slug/orders/:token/pay", destination: `${API}/public/store/:slug/orders/:token/pay` },
      { source: "/storefront/api/:slug/orders/:token/cancel", destination: `${API}/public/store/:slug/orders/:token/cancel` },
    ];
  },
};

export default nextConfig;
