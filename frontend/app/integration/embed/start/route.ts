import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const origin = new URL(process.env.FM_JOOLUN_EMBED_BASE_URL || request.url).origin;
  const response = NextResponse.redirect(new URL("/integration/embed/error?code=legacy-entry-disabled", origin));
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
