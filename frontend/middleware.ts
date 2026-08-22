import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

const INTERNAL_API_PREFIX = "/api/internal";
const V2_API_PREFIX = "/api/v2";
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
  if (request.nextUrl.pathname === V2_API_PREFIX || request.nextUrl.pathname.startsWith(`${V2_API_PREFIX}/`)) {
    const token = process.env.FM_API_V2_BEARER_TOKEN?.trim();
    if (!token) {
      return NextResponse.json(
        {
          type: "urn:fm-ai:problem:frontend-integration-not-configured",
          title: "Frontend integration not configured",
          status: 503,
          detail: "对接工作台尚未配置服务端访问令牌，请联系系统管理员。",
          instance: request.nextUrl.pathname,
          code: "FRONTEND_INTEGRATION_NOT_CONFIGURED",
          errors: []
        },
        {
          status: 503,
          headers: { "Content-Type": "application/problem+json" }
        }
      );
    }
    const requestHeaders = new Headers(request.headers);
    requestHeaders.set("Authorization", `Bearer ${token}`);
    return NextResponse.rewrite(buildBackendUrl(request), {
      request: { headers: requestHeaders }
    });
  }
  return NextResponse.rewrite(buildBackendUrl(request));
}

export const config = {
  matcher: [
    "/api/internal/:path*",
    "/api/v1/:path*",
    "/api/v2/:path*",
    "/health/:path*",
    "/docs/:path*",
    "/redoc/:path*",
    "/openapi.json"
  ],
  runtime: "nodejs"
};
