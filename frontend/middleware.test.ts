import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  getRewrittenUrl,
  isRewrite,
  unstable_doesMiddlewareMatch
} from "next/experimental/testing/server";
import { NextRequest } from "next/server";

import nextConfig from "./next.config";
import { buildBackendUrl, config, middleware } from "./middleware";

describe("API proxy middleware", () => {
  const previousBaseUrl = process.env.INTERNAL_API_BASE_URL;
  const previousEmbedOrigins = process.env.FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS;
  const previousEmbedBaseUrl = process.env.FM_JOOLUN_EMBED_BASE_URL;
  const previousAdminOrigins = process.env.FM_INTERNAL_ADMIN_ORIGINS;

  beforeEach(() => {
    process.env.INTERNAL_API_BASE_URL = "http://backend:8000";
    process.env.FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS = "http://localhost:7600";
    process.env.FM_JOOLUN_EMBED_BASE_URL = "https://fm.example.com";
    delete process.env.FM_INTERNAL_ADMIN_ORIGINS;
  });

  afterEach(() => {
    if (previousAdminOrigins === undefined) delete process.env.FM_INTERNAL_ADMIN_ORIGINS;
    else process.env.FM_INTERNAL_ADMIN_ORIGINS = previousAdminOrigins;
    if (previousBaseUrl === undefined) {
      delete process.env.INTERNAL_API_BASE_URL;
    } else {
      process.env.INTERNAL_API_BASE_URL = previousBaseUrl;
    }
    if (previousEmbedOrigins === undefined) {
      delete process.env.FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS;
    } else {
      process.env.FM_JOOLUN_EMBED_ALLOWED_PARENT_ORIGINS = previousEmbedOrigins;
    }
    if (previousEmbedBaseUrl === undefined) {
      delete process.env.FM_JOOLUN_EMBED_BASE_URL;
    } else {
      process.env.FM_JOOLUN_EMBED_BASE_URL = previousEmbedBaseUrl;
    }
  });

  it("strips the internal API prefix and preserves the query string", () => {
    const request = new NextRequest(
      "https://fm.example.com/api/internal/cases/case_1?workspace=private"
    );

    expect(buildBackendUrl(request).toString()).toBe(
      "http://backend:8000/cases/case_1?workspace=private"
    );
    const response = middleware(request);
    expect(isRewrite(response)).toBe(true);
    expect(getRewrittenUrl(response)).toBe(
      "http://backend:8000/cases/case_1?workspace=private"
    );
  });

  it.each([
    ["/api/v1/cases/case_1", "http://backend:8000/api/v1/cases/case_1"],
    ["/api/v2/cases/case_1", "http://backend:8000/api/v2/cases/case_1"],
    ["/health", "http://backend:8000/health"],
    ["/health/rag", "http://backend:8000/health/rag"],
    ["/docs", "http://backend:8000/docs"],
    ["/redoc", "http://backend:8000/redoc"],
    ["/openapi.json", "http://backend:8000/openapi.json"]
  ])("preserves the public backend path %s", (path, expected) => {
    const request = new NextRequest(`https://fm.example.com${path}`);
    expect(buildBackendUrl(request).toString()).toBe(expected);
  });

  it("converts the HttpOnly session into the upstream bearer identity and discards browser authorization", () => {
    const request = new NextRequest("https://fm.example.com/api/v2/cases/case_1", {
      headers: {
        Authorization: "Bearer browser-controlled-value",
        Cookie: "fm_session=doctor-session-token"
      }
    });
    const response = middleware(request);

    expect(isRewrite(response)).toBe(true);
    expect(getRewrittenUrl(response)).toBe("http://backend:8000/api/v2/cases/case_1");
    expect(response.headers.get("x-middleware-request-authorization")).toBe(
      "Bearer doctor-session-token"
    );
    expect(response.headers.get("authorization")).toBeNull();
  });

  it("returns an authentication problem when the session is missing", async () => {
    const request = new NextRequest("https://fm.example.com/api/v2/cases/case_1");
    const response = middleware(request);

    expect(response.status).toBe(401);
    expect(response.headers.get("content-type")).toContain("application/problem+json");
    await expect(response.json()).resolves.toMatchObject({
      code: "AUTHENTICATION_REQUIRED",
      status: 401
    });
  });

  it("accepts same-origin writes and rejects cross-origin writes", async () => {
    const accepted = middleware(new NextRequest("https://fm.example.com/api/v2/cases", {
      method: "POST",
      headers: {
        Cookie: "fm_session=doctor-session-token",
        Origin: "https://fm.example.com",
        "Sec-Fetch-Site": "same-origin"
      }
    }));
    expect(isRewrite(accepted)).toBe(true);

    const rejected = middleware(new NextRequest("https://fm.example.com/api/v2/cases", {
      method: "POST",
      headers: {
        Cookie: "fm_session=doctor-session-token",
        Origin: "https://attacker.example",
        "Sec-Fetch-Site": "cross-site"
      }
    }));
    expect(rejected.status).toBe(403);
    await expect(rejected.json()).resolves.toMatchObject({
      code: "CROSS_ORIGIN_REQUEST_REJECTED",
      status: 403
    });
  });

  it("uses the configured public origin when Docker receives its internal port", () => {
    process.env.FM_JOOLUN_EMBED_BASE_URL = "http://localhost:18080";
    const accepted = middleware(new NextRequest("http://localhost:3000/api/v2/cases/case_1", {
      method: "POST",
      headers: {
        Cookie: "fm_session=doctor-session-token",
        Origin: "http://localhost:18080",
        "Sec-Fetch-Site": "same-origin"
      }
    }));

    expect(isRewrite(accepted)).toBe(true);
    expect(getRewrittenUrl(accepted)).toBe("http://backend:8000/api/v2/cases/case_1");
  });

  it("allows the 8600 entry and explicitly configured private admin entry", () => {
    process.env.FM_JOOLUN_EMBED_BASE_URL = "https://paracelsus.huapont.com.cn:8600";
    process.env.FM_INTERNAL_ADMIN_ORIGINS = " https://paracelsus.huapont.com.cn:3200/ ";
    for (const origin of ["https://paracelsus.huapont.com.cn:8600", "https://paracelsus.huapont.com.cn:3200"]) {
      const response = middleware(new NextRequest("http://localhost:3000/api/v2/cases", {
        method: "POST",
        headers: { Cookie: "fm_session=admin-session", Origin: origin, "Sec-Fetch-Site": "same-origin" }
      }));
      expect(isRewrite(response)).toBe(true);
      expect(response.headers.get("x-middleware-request-authorization")).toBe("Bearer admin-session");
    }
    const frame = middleware(new NextRequest("http://localhost:3000/integration/embed/cases/case_1"));
    expect(frame.headers.get("content-security-policy")).not.toContain(":3200");
  });

  it.each([
    ["", "https://admin.example.com", "same-origin", true, 403],
    ["https://admin.example.com", "https://admin.example.com", "cross-site", true, 403],
    ["https://admin.example.com", "https://admin.example.com", "same-site", true, 403],
    ["https://admin.example.com", "https://evil.example.com", "same-origin", true, 403],
    ["https://admin.example.com", "", "same-origin", true, 403],
    ["https://admin.example.com/path", "https://admin.example.com", "same-origin", true, 403],
    ["https://user:pass@admin.example.com", "https://admin.example.com", "same-origin", true, 403],
    ["*", "https://admin.example.com", "same-origin", true, 403],
    ["https://admin.example.com", "https://admin.example.com", "same-origin", false, 401]
  ])("does not bypass origin or session checks (%s, %s, %s)", (configured, origin, site, session, status) => {
    process.env.FM_INTERNAL_ADMIN_ORIGINS = configured as string;
    const response = middleware(new NextRequest("http://localhost:3000/api/v2/cases", {
      method: "POST",
      headers: { ...(session ? { Cookie: "fm_session=admin-session" } : {}), Origin: origin as string, "Sec-Fetch-Site": site as string }
    }));
    expect(response.status).toBe(status);
  });

  it.each(["/cases/case_1", "/assistant", "/products"])(
    "does not match the Next.js page %s",
    (url) => {
      expect(
        unstable_doesMiddlewareMatch({
          config,
          nextConfig,
          url
        })
      ).toBe(false);
    }
  );

  it("allows only configured parents to frame embedded workflow pages", () => {
    const response = middleware(
      new NextRequest("http://localhost:18080/integration/embed/cases/case_1")
    );

    expect(response.headers.get("content-security-policy")).toBe(
      "frame-ancestors http://localhost:7600"
    );
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it.each(["/api/internal/auth/me", "/api/v1/auth/token", "/api/v2/cases/case_1", "/health", "/docs", "/integration/embed/cases/case_1"])(
    "matches the backend path %s",
    (url) => {
      expect(
        unstable_doesMiddlewareMatch({
          config,
          nextConfig,
          url
        })
      ).toBe(true);
    }
  );
});
