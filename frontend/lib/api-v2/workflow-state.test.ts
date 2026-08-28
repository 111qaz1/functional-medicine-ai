import { describe, expect, it } from "vitest";

import type { AnalysisResponse, CaseResponse, DraftResponse, OperationResponse, ReportResponse } from "./types";
import {
  currentWorkflowStep,
  deriveWorkflowSteps,
  resolveAnalysisCompletionNavigation,
  resolveDraftCompletionNavigation,
  resolveRequestedWorkflowStep,
  resolveWorkflowBlockGuidance
} from "./workflow-state";

const caseResource = {
  id: "case_1",
  attachments: [{ id: "file_1" }]
} as CaseResponse;

const analysis = {
  id: "analysis_1",
  status: "ready_for_review",
  draft_generation: { status: "idle" }
} as AnalysisResponse;

describe("workflow state", () => {
  it("blocks downstream steps until a case exists", () => {
    const steps = deriveWorkflowSteps({ caseResource: null, analysis: null, draft: null, report: null });
    expect(currentWorkflowStep(steps)).toBe("case");
    expect(steps.slice(1).every((step) => step.state === "blocked")).toBe(true);
  });

  it("makes review current when analysis is ready", () => {
    const steps = deriveWorkflowSteps({ caseResource, analysis, draft: null, report: null });
    expect(currentWorkflowStep(steps)).toBe("review");
    expect(steps).toHaveLength(5);
    expect(steps.find((step) => step.id === "attachments")?.state).toBe("complete");
  });

  it("keeps attachments current after the last persisted attachment is removed", () => {
    const emptyAfterDelete = {
      ...caseResource,
      status: "parsing_completed",
      attachments: []
    } as CaseResponse;
    const staleAnalysis = { ...analysis, status: "stale" } as AnalysisResponse;
    const steps = deriveWorkflowSteps({ caseResource: emptyAfterDelete, analysis: staleAnalysis, draft: null, report: null });

    expect(currentWorkflowStep(steps)).toBe("attachments");
    expect(steps.find((step) => step.id === "attachments")?.state).toBe("current");
    expect(steps.find((step) => step.id === "review")?.state).toBe("blocked");
  });

  it("keeps analysis start, running and failure states on attachments", () => {
    const waiting = deriveWorkflowSteps({ caseResource, analysis: null, draft: null, report: null });
    expect(currentWorkflowStep(waiting)).toBe("attachments");

    const running = deriveWorkflowSteps({
      caseResource,
      analysis: { ...analysis, status: "analyzing_documents" } as AnalysisResponse,
      draft: null,
      report: null
    });
    expect(currentWorkflowStep(running)).toBe("attachments");
    expect(running.find((step) => step.id === "review")?.state).toBe("blocked");

    const failed = deriveWorkflowSteps({
      caseResource,
      analysis: { ...analysis, status: "failed" } as AnalysisResponse,
      draft: null,
      report: null
    });
    expect(failed.find((step) => step.id === "attachments")?.state).toBe("error");
  });

  it("explains the earliest unmet prerequisite for a locked step", () => {
    const emptyCase = { ...caseResource, attachments: [] } as CaseResponse;
    expect(resolveWorkflowBlockGuidance(
      { caseResource: emptyCase, analysis: null, draft: null, report: null },
      "review"
    )).toMatchObject({ reason: "no_attachments", actionStep: "attachments" });

    expect(resolveWorkflowBlockGuidance(
      { caseResource, analysis: null, draft: null, report: null },
      "draft"
    )).toMatchObject({ reason: "analysis_not_started", actionStep: "attachments" });

    expect(resolveWorkflowBlockGuidance(
      {
        caseResource,
        analysis: { ...analysis, status: "analyzing_documents" } as AnalysisResponse,
        draft: null,
        report: null
      },
      "report"
    )).toMatchObject({ reason: "analysis_running", actionStep: "attachments" });
  });

  it("keeps draft progress and failure on review with a usable recovery target", () => {
    const generatingAnalysis = {
      ...analysis,
      draft_generation: { status: "mapping_products", progress: 60, error: null }
    } as AnalysisResponse;
    expect(resolveWorkflowBlockGuidance(
      { caseResource, analysis: generatingAnalysis, draft: null, report: null },
      "draft"
    )).toMatchObject({ reason: "draft_generating", actionStep: "review", anchorId: "workflow-draft-progress" });

    const failedAnalysis = {
      ...analysis,
      draft_generation: { status: "failed", progress: 60, error: "生成失败" }
    } as AnalysisResponse;
    const steps = deriveWorkflowSteps({ caseResource, analysis: failedAnalysis, draft: null, report: null });
    expect(steps.find((step) => step.id === "review")?.state).toBe("error");
    expect(steps.find((step) => step.id === "draft")?.state).toBe("blocked");
    expect(resolveWorkflowBlockGuidance(
      { caseResource, analysis: failedAnalysis, draft: null, report: null },
      "draft"
    )).toMatchObject({ reason: "draft_failed", actionStep: "review", anchorId: "workflow-draft-retry" });
  });

  it("maps the removed analysis URL to the matching five-step page", () => {
    const waiting = deriveWorkflowSteps({ caseResource, analysis: null, draft: null, report: null });
    expect(resolveRequestedWorkflowStep("analysis", waiting, "attachments", false)).toBe("attachments");

    const ready = deriveWorkflowSteps({ caseResource, analysis, draft: null, report: null });
    expect(resolveRequestedWorkflowStep("analysis", ready, "review", true)).toBe("review");
  });

  it("moves to report after approval and completes after report recovery", () => {
    const draft = { id: "draft_1", status: "approved" } as DraftResponse;
    const pendingReportSteps = deriveWorkflowSteps({ caseResource, analysis, draft, report: null });
    expect(currentWorkflowStep(pendingReportSteps)).toBe("report");

    const report = { draft_id: "draft_1", status: "ready" } as ReportResponse;
    const completeSteps = deriveWorkflowSteps({ caseResource, analysis, draft, report });
    expect(completeSteps.find((step) => step.id === "report")?.state).toBe("complete");
  });

  it("allows final report editing as soon as a draft exists", () => {
    const draft = { id: "draft_1", status: "pending_review" } as DraftResponse;
    const steps = deriveWorkflowSteps({ caseResource, analysis, draft, report: null });
    expect(steps.find((step) => step.id === "report")?.state).toBe("available");
  });

  it("auto-advances a completed analysis operation only once", () => {
    const operation = {
      operation_id: "operation_analysis_1",
      stage: "analysis",
      status: "succeeded"
    } as OperationResponse;

    const firstCompletion = resolveAnalysisCompletionNavigation(operation, true, "attachments", null);
    expect(firstCompletion).toEqual({
      operationId: "operation_analysis_1",
      nextStep: "review"
    });

    expect(
      resolveAnalysisCompletionNavigation(operation, true, "attachments", firstCompletion?.operationId ?? null)
    ).toBeNull();
  });

  it("marks analysis completion handled without stealing later navigation", () => {
    const operation = {
      operation_id: "operation_analysis_2",
      stage: "analysis",
      status: "succeeded"
    } as OperationResponse;

    expect(resolveAnalysisCompletionNavigation(operation, true, "review", null)).toEqual({
      operationId: "operation_analysis_2",
      nextStep: null
    });
  });

  it("auto-advances a completed draft operation only once", () => {
    const operation = {
      operation_id: "operation_draft_1",
      stage: "draft_generation",
      status: "succeeded"
    } as OperationResponse;

    const firstCompletion = resolveDraftCompletionNavigation(operation, true, "review", null);
    expect(firstCompletion).toEqual({
      operationId: "operation_draft_1",
      nextStep: "draft"
    });

    expect(
      resolveDraftCompletionNavigation(operation, true, "review", firstCompletion?.operationId ?? null)
    ).toBeNull();
  });

  it("marks a completed draft operation handled without stealing later navigation", () => {
    const operation = {
      operation_id: "operation_draft_2",
      stage: "draft_generation",
      status: "succeeded"
    } as OperationResponse;

    expect(resolveDraftCompletionNavigation(operation, true, "case", null)).toEqual({
      operationId: "operation_draft_2",
      nextStep: null
    });
  });
});
