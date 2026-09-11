import { IntegrationCaseWorkbench } from "../../../../../components/integration/integration-case-workbench";
import { DoctorSessionGate } from "../../../../../components/integration/doctor-session";

export default async function EmbeddedIntegrationCasePage({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = await params;
  return (
    <DoctorSessionGate embedded>
      <IntegrationCaseWorkbench caseId={caseId} fixtureMode={false} fixtureScenario="success" embedded />
    </DoctorSessionGate>
  );
}
