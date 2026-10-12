import { NextRequest, NextResponse } from "next/server";

const BACKEND_BASE_URL = process.env.INTERNAL_API_BASE_URL?.trim() || "http://127.0.0.1:8000";

function embedPublicBaseUrl(request: NextRequest): URL {
  const configured = process.env.FM_JOOLUN_EMBED_BASE_URL?.trim();
  if (!configured) return new URL(request.nextUrl.origin);
  try {
    const url = new URL(configured);
    if (!["http:", "https:"].includes(url.protocol)) return new URL(request.nextUrl.origin);
    return new URL(url.origin);
  } catch {
    return new URL(request.nextUrl.origin);
  }
}

export async function GET(request: NextRequest) {
  const publicBase = embedPublicBaseUrl(request);
  function redirectTo(path: string) {
    const response = NextResponse.redirect(new URL(path, publicBase));
    response.headers.set("Referrer-Policy", "no-referrer");
    response.headers.set("Cache-Control", "no-store");
    return response;
  }
  const errorRedirect = (code: string) => redirectTo(`/integration/embed/error?code=${code}`);
  const ticket = request.nextUrl.searchParams.get("ticket")?.trim();
  if (!ticket) {
    return errorRedirect("missing-ticket");
  }

  let response: Response;
  try {
    response = await fetch(`${BACKEND_BASE_URL}/api/v2/integrations/joolun/embed-tickets:exchange`, {
      method: "POST",
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
      headers: { "Content-Type": "application/json", Accept: "application/json, application/problem+json" },
      body: JSON.stringify({ ticket })
    });
  } catch {
    return errorRedirect("backend-unavailable");
  }
  if (!response.ok) {
    return errorRedirect(response.status >= 500 ? "backend-unavailable" : "invalid-ticket");
  }

  let payload: { access_token?: unknown; case_id?: unknown; expires_at?: unknown } | null;
  try {
    payload = await response.json();
  } catch {
    return errorRedirect("invalid-response");
  }
  if (
    !payload ||
    typeof payload.access_token !== "string" || !payload.access_token.trim() ||
    typeof payload.case_id !== "string" || !payload.case_id.trim() ||
    typeof payload.expires_at !== "string" ||
    !Number.isFinite(Date.parse(payload.expires_at)) ||
    Date.parse(payload.expires_at) <= Date.now()
  ) {
    return errorRedirect("invalid-response");
  }

  const redirect = redirectTo(`/integration/embed/cases/${encodeURIComponent(payload.case_id)}`);
  const secureSetting = process.env.FM_SESSION_COOKIE_SECURE?.trim().toLowerCase();
  const secure = secureSetting
    ? ["1", "true", "yes", "on"].includes(secureSetting)
    : publicBase.protocol === "https:";
  redirect.cookies.set("fm_session", payload.access_token, {
    httpOnly: true,
    secure,
    sameSite: secure ? "none" : "lax",
    path: "/",
    expires: new Date(payload.expires_at)
  });
  return redirect;
}
