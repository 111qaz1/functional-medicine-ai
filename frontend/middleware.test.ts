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

  beforeEach(() => {
    process.env.INTERNAL_API_BASE_URL = "http://backend:8000";
  });

  afterEach(() => {
    if (previousBaseUrl === undefined) {
      delete process.env.INTERNAL_API_BASE_URL;
    } else {
      process.env.INTERNAL_API_BASE_URL = previousBaseUrl;
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
    ["/health", "http://backend:8000/health"],
    ["/health/rag", "http://backend:8000/health/rag"],
    ["/docs", "http://backend:8000/docs"],
    ["/redoc", "http://backend:8000/redoc"],
    ["/openapi.json", "http://backend:8000/openapi.json"]
  ])("preserves the public backend path %s", (path, expected) => {
    const request = new NextRequest(`https://fm.example.com${path}`);
    expect(buildBackendUrl(request).toString()).toBe(expected);
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

  it.each(["/api/internal/auth/me", "/api/v1/auth/token", "/health", "/docs"])(
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
