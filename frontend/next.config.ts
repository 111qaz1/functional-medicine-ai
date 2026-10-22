import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  typedRoutes: true,
  experimental: {
    // Allow the backend's 100 MiB file batch plus multipart headers/boundaries.
    middlewareClientMaxBodySize: "110mb",
    proxyTimeout: 900_000
  }
};

export default nextConfig;
