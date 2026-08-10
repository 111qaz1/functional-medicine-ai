import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

const INTERNAL_API_PREFIX = "/api/internal";
const DEFAULT_INTERNAL_API_BASE_URL = "http://127.0.0.1:8000";

export function buildBackendUrl(request: NextRequest): URL {
  const backendBaseUrl =
    process.env.INTERNAL_API_BASE_URL?.trim() || DEFAULT_INTERNAL_API_BASE_URL;
  const pathname = request.nextUrl.pathname;
  const backendPath = pathname.startsWith(`${INTERNAL_API_PREFIX}/`)
    ? pathname.slice(INTERNAL_API_PREFIX.length)
    : pathname === INTERNAL_API_PREFIX
      ? "/"
      : pathname;
  const targetUrl = new URL(backendBaseUrl);
  targetUrl.pathname = backendPath;
  targetUrl.search = request.nextUrl.search;
  targetUrl.hash = "";
  return targetUrl;
}

export function middleware(request: NextRequest) {
  return NextResponse.rewrite(buildBackendUrl(request));
}

export const config = {
  matcher: [
    "/api/internal/:path*",
    "/api/v1/:path*",
    "/health/:path*",
    "/docs/:path*",
    "/redoc/:path*",
    "/openapi.json"
  ],
  runtime: "nodejs"
};
