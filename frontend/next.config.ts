import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  typedRoutes: true,
  experimental: {
    // Allow a 100 MiB batch plus multipart overhead; FastAPI enforces business limits.
    middlewareClientMaxBodySize: "110mb",
    proxyTimeout: 900_000
  }
};

export default nextConfig;
