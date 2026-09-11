import { IntegrationCaseWorkbench } from "../../../../components/integration/integration-case-workbench";
import { DoctorSessionGate } from "../../../../components/integration/doctor-session";
import { getFixtureFrontendConfig } from "../../../../lib/api-v2/fixture-config";

export default async function IntegrationCasePage({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = await params;
  const fixture = getFixtureFrontendConfig();
  return (
    <DoctorSessionGate>
      <IntegrationCaseWorkbench caseId={caseId} fixtureMode={fixture.enabled} fixtureScenario={fixture.scenario} />
    </DoctorSessionGate>
  );
}
