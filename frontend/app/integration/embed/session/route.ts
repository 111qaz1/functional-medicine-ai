import { NextRequest, NextResponse } from "next/server";

const backend = () => (process.env.INTERNAL_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const publicOrigin = (request: NextRequest) => new URL(process.env.FM_JOOLUN_EMBED_BASE_URL || request.url).origin;

function json(body: unknown, status = 200) {
  return NextResponse.json(body, { status, headers: { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" } });
}

async function handleSession(request: NextRequest, creating: boolean) {
  if (creating && (request.headers.get("origin") !== publicOrigin(request) ||
      ![null, "same-origin"].includes(request.headers.get("sec-fetch-site")))) {
    return json({ detail: "只允许从当前医生工作台建立会话。" }, 403);
  }
  const javaToken = request.cookies.get("Admin-Token")?.value;
  const session = request.cookies.get("fm_session")?.value;
  if (!javaToken || (!creating && !session)) return json({ detail: "请先登录甲方平台。" }, 401);
  const authorization = request.headers.get("authorization");
  if (creating && authorization !== `Bearer ${javaToken}`) return json({ detail: "登录凭据不一致，请刷新后重试。" }, 401);
  let payload: Record<string, unknown> | undefined;
  if (creating) {
    try {
      const data = await request.json();
      if (!data || ["external_patient_id", "external_encounter_id", "patient_name"].some(key =>
        typeof data[key] !== "string" || !data[key].trim() || data[key].length > 160)) {
        return json({ detail: "患者或分析信息不完整。" }, 400);
      }
      payload = { external_patient_id: data.external_patient_id.trim(),
        external_encounter_id: data.external_encounter_id.trim(), patient_name: data.patient_name.trim(),
        parent_origin: publicOrigin(request) };
    } catch { return json({ detail: "请求格式无效。" }, 400); }
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${backend()}/api/v2/integrations/joolun/token-sessions`, {
      method: creating ? "POST" : "GET", cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(15_000),
      headers: { "Content-Type": "application/json", "X-FM-Session-BFF": "1", "Authorization": `Bearer ${creating ? javaToken : session}`,
        Cookie: `Admin-Token=${encodeURIComponent(javaToken)}${session ? `; fm_session=${encodeURIComponent(session)}` : ""}` },
      ...(creating ? { body: JSON.stringify(payload) } : {})
    });
  } catch { return json({ detail: "登录核验暂不可用，请稍后重试；病例进度已保留。" }, 503); }
  let data;
  try { data = await upstream.json(); } catch { return json({ detail: "登录核验返回了无效响应。" }, 503); }
  if (!upstream.ok) return json({ detail: data?.detail || "登录核验失败。", code: data?.code }, upstream.status);
  if (typeof data?.access_token !== "string" || !data.access_token.trim() || typeof data.case_id !== "string" || !data.case_id.trim() ||
      typeof data.doctor?.id !== "string" || typeof data.doctor?.username !== "string" ||
      typeof data.doctor?.display_name !== "string" || data.doctor?.role !== "doctor" || data.doctor?.enabled !== true ||
      typeof data.external_encounter_id !== "string" || !data.external_encounter_id.trim() || typeof data.read_only !== "boolean" ||
      typeof data.expires_at !== "string" || !Number.isFinite(Date.parse(data.expires_at)) || Date.parse(data.expires_at) <= Date.now()) {
    return json({ detail: "登录核验返回了无效响应。" }, 503);
  }
  const { id, username, display_name, role, enabled } = data.doctor;
  const result = json({ doctor: { id, username, display_name, role, enabled }, case_id: data.case_id, expires_at: data.expires_at,
    external_encounter_id: data.external_encounter_id, read_only: data.read_only,
    embed_url: new URL(`/integration/embed/cases/${encodeURIComponent(data.case_id)}`, publicOrigin(request)).toString() });
  if (creating) {
    const secure = publicOrigin(request).startsWith("https:");
    result.cookies.set("fm_session", data.access_token, { httpOnly: true, secure, sameSite: "lax", path: "/", expires: new Date(data.expires_at) });
  }
  return result;
}

export const POST = (request: NextRequest) => handleSession(request, true);
export const GET = (request: NextRequest) => handleSession(request, false);
