import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "./route";

const publicOrigin = "https://paracelsus.huapont.com.cn:8600";
const request = (ticket = "single-use-ticket") =>
  new NextRequest(`http://localhost:3000/integration/embed/start${ticket ? `?ticket=${ticket}` : ""}`);
const validPayload = () => ({
  access_token: "test-session",
  case_id: "case_1",
  expires_at: new Date(Date.now() + 3600_000).toISOString()
});

describe("embedded ticket exchange behind the shared public entry", () => {
  beforeEach(() => {
    vi.stubEnv("FM_JOOLUN_EMBED_BASE_URL", publicOrigin);
    vi.stubEnv("FM_SESSION_COOKIE_SECURE", "1");
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  async function expectError(code: string, input = request()) {
    const response = await GET(input);
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe(`${publicOrigin}/integration/embed/error?code=${code}`);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(response.headers.has("set-cookie")).toBe(false);
  }

  it("sets the HttpOnly session and redirects to the public case URL", async () => {
    vi.mocked(fetch).mockResolvedValue(Response.json(validPayload()));
    const response = await GET(request());
    expect(response.headers.get("location")).toBe(`${publicOrigin}/integration/embed/cases/case_1`);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    const cookie = response.cookies.get("fm_session");
    expect(cookie).toMatchObject({ value: "test-session", httpOnly: true, secure: true, sameSite: "none", path: "/" });
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/api/v2/integrations/joolun/embed-tickets:exchange"), expect.objectContaining({
      method: "POST", cache: "no-store", body: JSON.stringify({ ticket: "single-use-ticket" }), signal: expect.any(AbortSignal)
    }));
  });

  it("does not call the backend without a ticket", async () => {
    await expectError("missing-ticket", request(""));
    expect(fetch).not.toHaveBeenCalled();
  });

  it("handles an expired or consumed ticket", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("", { status: 401 }));
    await expectError("invalid-ticket");
  });

  it("handles a backend error", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("", { status: 503 }));
    await expectError("backend-unavailable");
  });

  it.each([new TypeError("network unavailable"), new DOMException("timeout", "TimeoutError")])(
    "handles a rejected exchange without retrying a one-time ticket", async (error) => {
      vi.mocked(fetch).mockRejectedValue(error);
      await expectError("backend-unavailable");
      expect(fetch).toHaveBeenCalledOnce();
    }
  );

  it("handles non-JSON responses", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("<html>upstream error</html>"));
    await expectError("invalid-response");
  });

  it.each([
    null, {}, { access_token: 123, case_id: "case_1", expires_at: "2099-01-01" },
    { access_token: "test", case_id: " ", expires_at: "2099-01-01" },
    { access_token: "test", case_id: "case_1", expires_at: "not-a-date" },
    { access_token: "test", case_id: "case_1", expires_at: "2000-01-01" }
  ])("rejects an invalid session payload: %j", async (payload) => {
    vi.mocked(fetch).mockResolvedValue(Response.json(payload));
    await expectError("invalid-response");
  });

  it("infers secure cookies from the configured HTTPS origin behind HTTP upstream", async () => {
    vi.stubEnv("FM_SESSION_COOKIE_SECURE", "");
    vi.mocked(fetch).mockResolvedValue(Response.json(validPayload()));
    const response = await GET(request());
    expect(response.cookies.get("fm_session")?.secure).toBe(true);
  });
});
