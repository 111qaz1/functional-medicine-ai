import { describe, expect, it } from "vitest";

import type { AnalysisResponse, CaseResponse, DraftResponse, ReportResponse } from "./types";
import { currentWorkflowStep, deriveWorkflowSteps } from "./workflow-state";

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
    expect(steps.find((step) => step.id === "analysis")?.state).toBe("complete");
  });

  it("moves to report after approval and completes after report recovery", () => {
    const draft = { id: "draft_1", status: "approved" } as DraftResponse;
    const pendingReportSteps = deriveWorkflowSteps({ caseResource, analysis, draft, report: null });
    expect(currentWorkflowStep(pendingReportSteps)).toBe("report");

    const report = { draft_id: "draft_1", status: "ready" } as ReportResponse;
    const completeSteps = deriveWorkflowSteps({ caseResource, analysis, draft, report });
    expect(completeSteps.find((step) => step.id === "report")?.state).toBe("complete");
  });
});
