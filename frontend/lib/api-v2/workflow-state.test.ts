import { describe, expect, it } from "vitest";

import type { AnalysisResponse, CaseResponse, DraftResponse, ReportResponse } from "./types";
import { currentWorkflowStep, deriveWorkflowSteps, resolveRequestedWorkflowStep } from "./workflow-state";

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
});
