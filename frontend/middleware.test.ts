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
  const previousV2Token = process.env.FM_API_V2_BEARER_TOKEN;

  beforeEach(() => {
    process.env.INTERNAL_API_BASE_URL = "http://backend:8000";
    process.env.FM_API_V2_BEARER_TOKEN = "fixture-v2-token";
  });

  afterEach(() => {
    if (previousBaseUrl === undefined) {
      delete process.env.INTERNAL_API_BASE_URL;
    } else {
      process.env.INTERNAL_API_BASE_URL = previousBaseUrl;
    }
    if (previousV2Token === undefined) {
      delete process.env.FM_API_V2_BEARER_TOKEN;
    } else {
      process.env.FM_API_V2_BEARER_TOKEN = previousV2Token;
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

  it("injects the server-only bearer token into v2 upstream requests", () => {
    const request = new NextRequest("https://fm.example.com/api/v2/cases/case_1", {
      headers: { Authorization: "Bearer browser-controlled-value" }
    });
    const response = middleware(request);

    expect(isRewrite(response)).toBe(true);
    expect(getRewrittenUrl(response)).toBe("http://backend:8000/api/v2/cases/case_1");
    expect(response.headers.get("x-middleware-request-authorization")).toBe(
      "Bearer fixture-v2-token"
    );
    expect(response.headers.get("authorization")).toBeNull();
  });

  it("returns an explicit problem response when the v2 token is missing", async () => {
    delete process.env.FM_API_V2_BEARER_TOKEN;
    const request = new NextRequest("https://fm.example.com/api/v2/cases/case_1");
    const response = middleware(request);

    expect(response.status).toBe(503);
    expect(response.headers.get("content-type")).toContain("application/problem+json");
    await expect(response.json()).resolves.toMatchObject({
      code: "FRONTEND_INTEGRATION_NOT_CONFIGURED",
      status: 503
    });
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

  it.each(["/api/internal/auth/me", "/api/v1/auth/token", "/api/v2/cases/case_1", "/health", "/docs"])(
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
