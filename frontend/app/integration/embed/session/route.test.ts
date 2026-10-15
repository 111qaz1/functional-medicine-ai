import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, POST } from "./route";

const origin = "https://partner.example:8600";
const body = { external_patient_id: "2099664879912943617", external_encounter_id: "encounter", patient_name: "Synthetic" };
const payload = () => ({ access_token: "private-ai-session", case_id: "case-1", external_encounter_id: "original-encounter", read_only: true, expires_at: new Date(Date.now()+3600000).toISOString(), doctor: { id: "doctor-1", username:"external:partner:1", display_name:"Synthetic", role:"doctor", enabled:true } });
function request(overrides: Record<string,string> = {}, method="POST") {
  return new NextRequest(`${origin}/integration/embed/session`, { method,
    headers: { Origin: origin, "Sec-Fetch-Site": "same-origin", Authorization: "Bearer java-token",
      Cookie: "Admin-Token=java-token; fm_session=previous-ai", ...overrides },
    ...(method === "POST" ? {body:JSON.stringify(body)} : {}) });
}
beforeEach(()=>{ vi.stubEnv("FM_JOOLUN_EMBED_BASE_URL", origin); vi.stubGlobal("fetch", vi.fn()); });
afterEach(()=>{vi.unstubAllEnvs();vi.unstubAllGlobals();});
it("sets only an HttpOnly AI cookie and never returns either credential", async()=>{
  vi.mocked(fetch).mockResolvedValue(Response.json(payload()));
  const response = await POST(request());
  expect(response.cookies.get("fm_session")).toMatchObject({value:"private-ai-session", httpOnly:true,secure:true,sameSite:"lax"});
  const result=await response.text();
  expect(result).not.toContain("private-ai-session"); expect(result).not.toContain("java-token");
  expect(JSON.parse(result)).toMatchObject({external_encounter_id:"original-encounter",read_only:true});
  expect(JSON.parse(result).embed_url).toBe(`${origin}/integration/embed/cases/case-1`);
  expect(response.headers.get("cache-control")).toBe("no-store");
  const [,init]=vi.mocked(fetch).mock.calls[0];
  expect(JSON.parse(String(init?.body))).toEqual({...body,parent_origin:origin});
  expect(init?.redirect).toBe("error");
});
it.each([{Origin:"https://evil.example"},{"Sec-Fetch-Site":"cross-site"}])("rejects cross-origin creation %j",async headers=>{
  expect((await POST(request(headers))).status).toBe(403);expect(fetch).not.toHaveBeenCalled();
});
it.each([{Cookie:""},{Authorization:"Bearer other"}])("rejects absent or inconsistent login %j",async headers=>{
  expect((await POST(request(headers))).status).toBe(401);expect(fetch).not.toHaveBeenCalled();
});
it("queries the current session without returning its bearer",async()=>{
  vi.mocked(fetch).mockResolvedValue(Response.json(payload()));
  const response=await GET(request({},"GET"));expect(response.status).toBe(200);
  expect((await response.text())).not.toContain("access_token");expect(response.cookies.get("fm_session")).toBeUndefined();
});
it("preserves upstream failures without setting a cookie",async()=>{
  vi.mocked(fetch).mockResolvedValue(Response.json({detail:"expired"},{status:401}));
  const response=await POST(request());expect(response.status).toBe(401);expect(response.cookies.get("fm_session")).toBeUndefined();
});
it.each([null,{}, {access_token:"token",case_id:"case",expires_at:"invalid"}])("rejects malformed upstream success %j",async data=>{
  vi.mocked(fetch).mockResolvedValue(Response.json(data));expect((await POST(request())).status).toBe(503);
});
it("handles network failure without exposing request credentials",async()=>{
  vi.mocked(fetch).mockRejectedValue(new Error("java-token"));
  const response=await POST(request());expect(response.status).toBe(503);expect(await response.text()).not.toContain("java-token");
});
