import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  typedRoutes: true,
  experimental: {
    middlewareClientMaxBodySize: "60mb",
    proxyTimeout: 900_000
  }
};

export default nextConfig;
