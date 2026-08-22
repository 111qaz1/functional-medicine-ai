import { IntegrationCaseWorkbench } from "../../../../components/integration/integration-case-workbench";

export default async function IntegrationCasePage({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = await params;
  return <IntegrationCaseWorkbench caseId={caseId} />;
}
