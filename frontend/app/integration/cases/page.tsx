import { IntegrationEntry } from "../../../components/integration/integration-entry";
import { getFixtureFrontendConfig } from "../../../lib/api-v2/fixture-config";

export default function IntegrationCasesPage() {
  const fixture = getFixtureFrontendConfig();
  return <IntegrationEntry fixtureMode={fixture.enabled} fixtureScenario={fixture.scenario} />;
}
