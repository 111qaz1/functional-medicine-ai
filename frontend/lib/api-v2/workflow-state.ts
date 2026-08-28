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

export type WorkflowBlockReason =
  | "no_attachments"
  | "analysis_not_started"
  | "analysis_running"
  | "analysis_failed_or_stale"
  | "review_required"
  | "draft_generating"
  | "draft_failed";

export interface WorkflowBlockGuidance {
  reason: WorkflowBlockReason;
  targetStep: WorkflowStepId;
  actionStep: WorkflowStepId;
  anchorId: string;
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
    review: !analysis
      ? "blocked"
      : analysisFailed
        ? "blocked"
        : reviewReady
          ? draft
            ? "complete"
            : draftFailed
              ? "error"
              : "current"
          : "blocked",
    draft: !reviewReady || !draft ? "blocked" : draft.status === "approved" ? "complete" : "current",
    report: !draft ? "blocked" : draft.status !== "approved" ? "available" : report ? "complete" : "current"
  };

  return workflowStepOrder.map((id) => ({ id, state: states[id] }));
}

export function resolveWorkflowBlockGuidance(
  resources: WorkflowResources,
  targetStep: WorkflowStepId
): WorkflowBlockGuidance | null {
  const { caseResource, analysis, draft } = resources;
  const target = deriveWorkflowSteps(resources).find((step) => step.id === targetStep);
  if (!target || target.state !== "blocked" || targetStep === "case") return null;

  if (!caseResource?.attachments.length) {
    return { reason: "no_attachments", targetStep, actionStep: "attachments", anchorId: "workflow-upload-selector" };
  }
  if (!analysis) {
    return { reason: "analysis_not_started", targetStep, actionStep: "attachments", anchorId: "workflow-analysis-launch" };
  }
  if (analysis.status === "failed" || analysis.status === "stale") {
    return { reason: "analysis_failed_or_stale", targetStep, actionStep: "attachments", anchorId: "workflow-analysis-launch" };
  }
  if (["queued", "preparing", "analyzing_documents", "synthesizing", "validating"].includes(analysis.status)) {
    return { reason: "analysis_running", targetStep, actionStep: "attachments", anchorId: "workflow-analysis-progress" };
  }

  if (!draft) {
    if (analysis.draft_generation.status === "failed") {
      return { reason: "draft_failed", targetStep, actionStep: "review", anchorId: "workflow-draft-retry" };
    }
    if ([
      "queued",
      "final_synthesizing",
      "validating_support_needs",
      "mapping_products",
      "checking_safety",
      "generating_draft",
      "ready"
    ].includes(analysis.draft_generation.status)) {
      return { reason: "draft_generating", targetStep, actionStep: "review", anchorId: "workflow-draft-progress" };
    }
    return { reason: "review_required", targetStep, actionStep: "review", anchorId: "workflow-review-editor" };
  }

  return null;
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
