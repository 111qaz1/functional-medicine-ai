import type { AnalysisResponse, CaseResponse, DraftResponse, ReportResponse } from "./types";

export type WorkflowStepId = "case" | "attachments" | "analysis" | "review" | "draft" | "report";
export type WorkflowStepState = "complete" | "current" | "available" | "blocked" | "error";

export interface WorkflowResources {
  caseResource: CaseResponse | null;
  analysis: AnalysisResponse | null;
  draft: DraftResponse | null;
  report: ReportResponse | null;
}

export interface WorkflowStep {
  id: WorkflowStepId;
  state: WorkflowStepState;
}

export const workflowStepOrder: WorkflowStepId[] = [
  "case",
  "attachments",
  "analysis",
  "review",
  "draft",
  "report"
];

export function deriveWorkflowSteps(resources: WorkflowResources): WorkflowStep[] {
  const { caseResource, analysis, draft, report } = resources;
  const hasAttachments = Boolean(
    caseResource && (caseResource.attachments.length > 0 || caseResource.status !== "intake")
  );
  const analysisFailed = analysis?.status === "failed" || analysis?.status === "stale";
  const reviewReady = analysis?.status === "ready_for_review" || analysis?.status === "reviewed";
  const draftFailed = analysis?.draft_generation.status === "failed";

  const states: Record<WorkflowStepId, WorkflowStepState> = {
    case: caseResource ? "complete" : "current",
    attachments: !caseResource ? "blocked" : hasAttachments ? "complete" : "current",
    analysis: !hasAttachments
      ? "blocked"
      : analysisFailed
        ? "error"
        : reviewReady
          ? "complete"
          : "current",
    review: !analysis ? "blocked" : analysisFailed ? "blocked" : reviewReady ? (draft ? "complete" : "current") : "blocked",
    draft: !reviewReady ? "blocked" : draftFailed ? "error" : draft ? (draft.status === "approved" ? "complete" : "current") : "blocked",
    report: draft?.status !== "approved" ? "blocked" : report ? "complete" : "current"
  };

  return workflowStepOrder.map((id) => ({ id, state: states[id] }));
}

export function currentWorkflowStep(steps: WorkflowStep[]): WorkflowStepId {
  return steps.find((step) => step.state === "current" || step.state === "error")?.id ?? "report";
}
