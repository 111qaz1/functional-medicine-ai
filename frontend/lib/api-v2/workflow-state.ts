import type { AnalysisResponse, CaseResponse, DraftResponse, OperationResponse, ReportResponse } from "./types";

export type WorkflowStepId = "case" | "attachments" | "review" | "draft" | "report";
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
    attachments: !caseResource
      ? "blocked"
      : !hasAttachments
        ? "current"
        : analysisFailed
          ? "error"
          : reviewReady
            ? "complete"
            : "current",
    review: !analysis ? "blocked" : analysisFailed ? "blocked" : reviewReady ? (draft ? "complete" : "current") : "blocked",
    draft: !reviewReady ? "blocked" : draftFailed ? "error" : draft ? (draft.status === "approved" ? "complete" : "current") : "blocked",
    report: !draft ? "blocked" : draft.status !== "approved" ? "available" : report ? "complete" : "current"
  };

  return workflowStepOrder.map((id) => ({ id, state: states[id] }));
}

export function currentWorkflowStep(steps: WorkflowStep[]): WorkflowStepId {
  return steps.find((step) => step.state === "current" || step.state === "error")?.id ?? "report";
}

export function resolveRequestedWorkflowStep(
  requested: string | null,
  steps: WorkflowStep[],
  fallback: WorkflowStepId,
  analysisReady: boolean
): WorkflowStepId {
  const normalized = requested === "analysis"
    ? (analysisReady ? "review" : "attachments")
    : requested;
  const target = steps.find((step) => step.id === normalized);
  return target && target.state !== "blocked" ? target.id : fallback;
}

export interface DraftCompletionNavigation {
  operationId: string;
  nextStep: "draft" | null;
}

export function resolveDraftCompletionNavigation(
  operation: OperationResponse | null,
  draftAvailable: boolean,
  visibleStep: WorkflowStepId,
  handledOperationId: string | null
): DraftCompletionNavigation | null {
  if (
    !operation ||
    operation.stage !== "draft_generation" ||
    operation.status !== "succeeded" ||
    !draftAvailable ||
    operation.operation_id === handledOperationId
  ) {
    return null;
  }

  return {
    operationId: operation.operation_id,
    nextStep: visibleStep === "review" ? "draft" : null
  };
}
