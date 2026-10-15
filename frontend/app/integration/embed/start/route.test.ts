import { expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "./route";
it("disables the old ticket entry without redeeming a ticket", async () => {
  vi.stubEnv("FM_JOOLUN_EMBED_BASE_URL", "https://partner.example:8600");
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  const response = await GET(new NextRequest("http://localhost:3000/integration/embed/start?ticket=old-ticket"));
  expect(response.headers.get("location")).toBe("https://partner.example:8600/integration/embed/error?code=legacy-entry-disabled");
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(fetchMock).not.toHaveBeenCalled();
  vi.unstubAllEnvs(); vi.unstubAllGlobals();
});
