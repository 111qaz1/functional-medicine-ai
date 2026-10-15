import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({ effects: [] as Array<() => unknown>, setters: [] as ReturnType<typeof vi.fn>[] }));
vi.mock("react", async importOriginal => {
  const actual = await importOriginal<typeof import("react")>();
  return { ...actual, useEffect: (effect: () => unknown) => hooks.effects.push(effect),
    useState: (initial: unknown) => { const set = vi.fn(); hooks.setters.push(set); return [initial,set]; } };
});
import { DoctorSessionGate } from "./doctor-session";

beforeEach(() => { hooks.effects=[]; hooks.setters=[]; vi.stubGlobal("fetch",vi.fn()); });
afterEach(() => vi.unstubAllGlobals());

it("embedded startup uses only the scoped session endpoint, never native login/bootstrap",async()=>{
  const doctor={id:"doctor",display_name:"Synthetic"};
  vi.mocked(fetch).mockResolvedValue(Response.json({doctor}));
  const html=renderToStaticMarkup(<DoctorSessionGate embedded><div>Workflow</div></DoctorSessionGate>);
  expect(html).not.toContain("password");
  hooks.effects[0]();
  await new Promise(resolve=>setTimeout(resolve,0));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledWith("/integration/embed/session",{credentials:"include",cache:"no-store"});
  expect(hooks.setters[0]).toHaveBeenCalledWith(doctor);
});
it("the private native management gate retains its existing login lookup",async()=>{
  vi.mocked(fetch).mockImplementation(async input=>Response.json(String(input).endsWith("bootstrap")?{required:false}:{doctor:null}));
  renderToStaticMarkup(<DoctorSessionGate><div>Workflow</div></DoctorSessionGate>);
  hooks.effects[0]();
  await new Promise(resolve=>setTimeout(resolve,0));
  expect(vi.mocked(fetch).mock.calls.map(call=>call[0])).toEqual(["/api/internal/auth/me","/api/internal/auth/bootstrap"]);
});
