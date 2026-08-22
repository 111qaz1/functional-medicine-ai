import { WorkflowSection, WorkflowShell } from "../../../components/integration/workflow-shell";
import { workflowCopy } from "../../../lib/api-v2/copy";

const entrySteps = [
  { id: "case", state: "current" },
  { id: "attachments", state: "blocked" },
  { id: "analysis", state: "blocked" },
  { id: "review", state: "blocked" },
  { id: "draft", state: "blocked" },
  { id: "report", state: "blocked" }
] as const;

export default function IntegrationCasesPage() {
  return (
    <WorkflowShell
      title={workflowCopy.entry.title}
      description={workflowCopy.entry.description}
      steps={[...entrySteps]}
      currentStep="case"
    >
      <WorkflowSection id="case" title="病例入口" state="current">
        <p className="workflow-placeholder">病例创建与恢复表单将在下一实施轮接入 v2 Gateway。</p>
      </WorkflowSection>
    </WorkflowShell>
  );
}
