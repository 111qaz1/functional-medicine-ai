import { IntegrationCaseWorkbench } from "../../../../components/integration/integration-case-workbench";
import { getFixtureFrontendConfig } from "../../../../lib/api-v2/fixture-config";

export default async function IntegrationCasePage({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = await params;
  const fixture = getFixtureFrontendConfig();
  return <IntegrationCaseWorkbench caseId={caseId} fixtureMode={fixture.enabled} fixtureScenario={fixture.scenario} />;
}
