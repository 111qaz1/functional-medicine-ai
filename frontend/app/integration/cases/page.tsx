import { IntegrationEntry } from "../../../components/integration/integration-entry";
import { DoctorSessionGate } from "../../../components/integration/doctor-session";
import { getFixtureFrontendConfig } from "../../../lib/api-v2/fixture-config";

export default function IntegrationCasesPage() {
  const fixture = getFixtureFrontendConfig();
  return (
    <DoctorSessionGate>
      <IntegrationEntry fixtureMode={fixture.enabled} fixtureScenario={fixture.scenario} />
    </DoctorSessionGate>
  );
}
