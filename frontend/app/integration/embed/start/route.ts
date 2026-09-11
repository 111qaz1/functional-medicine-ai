import { NextRequest, NextResponse } from "next/server";

const BACKEND_BASE_URL = process.env.INTERNAL_API_BASE_URL?.trim() || "http://127.0.0.1:8000";

export async function GET(request: NextRequest) {
  const ticket = request.nextUrl.searchParams.get("ticket")?.trim();
  if (!ticket) {
    return NextResponse.redirect(new URL("/integration/embed/error?code=missing-ticket", request.url));
  }

  const response = await fetch(`${BACKEND_BASE_URL}/api/v2/integrations/joolun/embed-tickets:exchange`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json", Accept: "application/json, application/problem+json" },
    body: JSON.stringify({ ticket })
  });
  if (!response.ok) {
    return NextResponse.redirect(new URL("/integration/embed/error?code=invalid-ticket", request.url));
  }

  const payload = await response.json() as { access_token?: string; case_id?: string; expires_at?: string };
  if (!payload.access_token || !payload.case_id || !payload.expires_at) {
    return NextResponse.redirect(new URL("/integration/embed/error?code=invalid-response", request.url));
  }

  const redirect = NextResponse.redirect(
    new URL(`/integration/embed/cases/${encodeURIComponent(payload.case_id)}`, request.url)
  );
  const secure = process.env.NODE_ENV === "production";
  redirect.cookies.set("fm_session", payload.access_token, {
    httpOnly: true,
    secure,
    sameSite: secure ? "none" : "lax",
    path: "/",
    expires: new Date(payload.expires_at)
  });
  redirect.headers.set("Referrer-Policy", "no-referrer");
  redirect.headers.set("Cache-Control", "no-store");
  return redirect;
}
