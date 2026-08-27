import { isFixtureScenario, type FixtureScenario } from "./fixture-gateway";

export interface FixtureFrontendConfig {
  enabled: boolean;
  scenario: FixtureScenario;
}

export function getFixtureFrontendConfig(): FixtureFrontendConfig {
  const enabled = process.env.NODE_ENV !== "production" && process.env.FM_WORKFLOW_FIXTURE_MODE === "1";
  const configuredScenario = process.env.FM_WORKFLOW_FIXTURE_SCENARIO;
  return {
    enabled,
    scenario: isFixtureScenario(configuredScenario) ? configuredScenario : "success"
  };
}
