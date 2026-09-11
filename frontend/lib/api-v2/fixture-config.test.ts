import { afterEach, describe, expect, it, vi } from "vitest";

import { getFixtureFrontendConfig } from "./fixture-config";

describe("fixture frontend config", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("enables a declared scenario in development", () => {
    vi.stubEnv("NODE_ENV", "development");
    vi.stubEnv("FM_WORKFLOW_FIXTURE_MODE", "1");
    vi.stubEnv("FM_WORKFLOW_FIXTURE_SCENARIO", "revision_conflict");
    expect(getFixtureFrontendConfig()).toEqual({ enabled: true, scenario: "revision_conflict" });
  });

  it("forces fixture mode off in production and defaults unknown scenarios", () => {
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("FM_WORKFLOW_FIXTURE_MODE", "1");
    vi.stubEnv("FM_WORKFLOW_FIXTURE_SCENARIO", "unknown");
    expect(getFixtureFrontendConfig()).toEqual({ enabled: false, scenario: "success" });
  });
});
