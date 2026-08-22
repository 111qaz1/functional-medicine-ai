import { WorkflowSection, WorkflowShell } from "../../../../components/integration/workflow-shell";
import { deriveWorkflowSteps } from "../../../../lib/api-v2/workflow-state";

export default async function IntegrationCasePage({ params }: { params: Promise<{ caseId: string }> }) {
  const { caseId } = await params;
  const steps = deriveWorkflowSteps({ caseResource: null, analysis: null, draft: null, report: null });
  return (
    <WorkflowShell title="病例工作流" caseId={caseId} steps={steps} currentStep="case">
      <WorkflowSection id="case" title="正在准备病例" state="current">
        <p className="workflow-placeholder">病例状态恢复与完整工作流将在下一实施轮接入。</p>
      </WorkflowSection>
    </WorkflowShell>
  );
}
