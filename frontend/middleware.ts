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
    const session = request.cookies.get("fm_session")?.value;
    if (!session) {
      return NextResponse.json(
        {
          type: "urn:fm-ai:problem:authentication-required",
          title: "Authentication required",
          status: 401,
          detail: "医生登录已失效，请重新登录。",
          instance: request.nextUrl.pathname,
          code: "AUTHENTICATION_REQUIRED",
          errors: []
        },
        {
          status: 401,
          headers: { "Content-Type": "application/problem+json" }
        }
      );
    }
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      const origin = request.headers.get("Origin");
      const fetchSite = request.headers.get("Sec-Fetch-Site");
      if (origin !== request.nextUrl.origin || (fetchSite && fetchSite !== "same-origin")) {
        return NextResponse.json(
          {
            type: "urn:fm-ai:problem:cross-origin-request-rejected",
            title: "Cross-origin request rejected",
            status: 403,
            detail: "写操作只允许从当前医生工作台发起。",
            instance: request.nextUrl.pathname,
            code: "CROSS_ORIGIN_REQUEST_REJECTED",
            errors: []
          },
          {
            status: 403,
            headers: { "Content-Type": "application/problem+json" }
          }
        );
      }
    }
    const requestHeaders = new Headers(request.headers);
    requestHeaders.delete("Authorization");
    requestHeaders.set("Authorization", `Bearer ${session}`);
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
