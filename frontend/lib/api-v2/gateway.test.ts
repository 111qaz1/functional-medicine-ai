import { describe, expect, it, vi } from "vitest";

import { HttpWorkflowGateway } from "./gateway";
import type { ProblemDetails } from "./types";

function jsonResponse(body: unknown, status = 200, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json", ...headers }
  });
}

describe("HttpWorkflowGateway", () => {
  it("uses the 13 declared v2 routes and preserves Location, multipart, and PDF metadata", async () => {
    const responses = [
      jsonResponse({ id: "case_1" }, 201),
      jsonResponse({ id: "case_1" }),
      jsonResponse({ id: "case_1", clinical_summary: "摘要" }),
      jsonResponse({ items: [], meta: { case_id: "case_1" } }, 201),
      jsonResponse({ operation_id: "analysis_1" }, 202, { Location: "/api/v2/operations/analysis_1" }),
      jsonResponse({ operation_id: "analysis_1" }),
      jsonResponse({ id: "analysis_1" }),
      jsonResponse({ operation_id: "analysis_1" }, 202, { Location: "/api/v2/operations/analysis_1" }),
      jsonResponse({ operation_id: "analysis_1" }, 202, { Location: "/api/v2/operations/analysis_1" }),
      jsonResponse({ id: "draft_1" }),
      jsonResponse({ draft_id: "draft_1" }),
      jsonResponse({ draft_id: "draft_1", status: "ready" }),
      new Response(new Blob(["%PDF-fixture"], { type: "application/pdf" }), {
        status: 200,
        headers: {
          "Content-Type": "application/pdf",
          "Content-Disposition": "attachment; filename*=UTF-8''fixture-report.pdf"
        }
      })
    ];
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      const response = responses.shift();
      if (!response) throw new Error("unexpected request");
      return response;
    }) as typeof fetch;
    const gateway = new HttpWorkflowGateway(fetchImpl);

    await gateway.createCase({ customer_name: "虚构用户", consultant_id: null, notes: null });
    await gateway.getCase("case_1");
    await gateway.updateClinicalSummary("case_1", { clinical_summary: "摘要" });
    await gateway.uploadAttachments(
      "case_1",
      "medical_record",
      [new File(["fixture"], "fixture.txt", { type: "text/plain" })]
    );
    const analysisOperation = await gateway.startAnalysis("case_1", {
      third_party_processing_confirmed: true
    });
    await gateway.getOperation("analysis_1");
    await gateway.getLatestAnalysis("case_1");
    const reviewOperation = await gateway.submitReview("case_1", "analysis_1", {
      reviewer_id: "doctor_1",
      expected_revision: 1,
      finding_changes: [],
      supplement_changes: [],
      food_sensitivity_changes: []
    });
    await gateway.retryDraftGeneration("case_1", "analysis_1");
    await gateway.getDraft("draft_1");
    await gateway.approveDraft("draft_1", {
      reviewer_id: "doctor_1",
      publishable_summary: null,
      excluded_sku_ids: [],
      dosage_overrides: []
    });
    await gateway.getReport("draft_1");
    const report = await gateway.downloadReport("draft_1");

    expect(calls.map(([url]) => String(url))).toEqual([
      "/api/v2/cases",
      "/api/v2/cases/case_1",
      "/api/v2/cases/case_1/clinical-summary",
      "/api/v2/cases/case_1/attachments",
      "/api/v2/cases/case_1/analyses",
      "/api/v2/operations/analysis_1",
      "/api/v2/cases/case_1/analyses/latest",
      "/api/v2/cases/case_1/analyses/analysis_1/reviews",
      "/api/v2/cases/case_1/analyses/analysis_1/draft-generation:retry",
      "/api/v2/drafts/draft_1",
      "/api/v2/drafts/draft_1/approval",
      "/api/v2/drafts/draft_1/report",
      "/api/v2/drafts/draft_1/report.pdf"
    ]);
    expect(calls.map(([, init]) => init?.method ?? "GET")).toEqual([
      "POST", "GET", "PUT", "POST", "POST", "GET", "GET", "POST", "POST", "GET", "POST", "GET", "GET"
    ]);
    const form = calls[3][1]?.body;
    expect(form).toBeInstanceOf(FormData);
    expect((form as FormData).get("attachment_type")).toBe("medical_record");
    expect(((form as FormData).get("files") as File).name).toBe("fixture.txt");
    expect(analysisOperation.location).toBe("/api/v2/operations/analysis_1");
    expect(reviewOperation.location).toBe("/api/v2/operations/analysis_1");
    expect(report.filename).toBe("fixture-report.pdf");
    expect(report.blob.type).toBe("application/pdf");
  });

  it("maps application/problem+json errors without exposing response text", async () => {
    const problem: ProblemDetails = {
      type: "urn:fm-ai:problem:analysis-revision-conflict",
      title: "Analysis revision conflict",
      status: 409,
      detail: "请重新获取后提交。",
      instance: "/api/v2/cases/case_1/analyses/analysis_1/reviews",
      code: "ANALYSIS_REVISION_CONFLICT",
      errors: []
    };
    const gateway = new HttpWorkflowGateway(
      vi.fn(async () => jsonResponse(problem, 409)) as typeof fetch
    );

    await expect(
      gateway.submitReview("case_1", "analysis_1", {
        reviewer_id: "doctor_1",
        expected_revision: 1,
        finding_changes: [],
        supplement_changes: [],
        food_sensitivity_changes: []
      })
    ).rejects.toMatchObject({
      name: "WorkflowProblem",
      problem
    });
  });

  it("normalizes a non-problem upstream error", async () => {
    const gateway = new HttpWorkflowGateway(
      vi.fn(async () => new Response("upstream stack trace", { status: 500 })) as typeof fetch
    );
    await expect(gateway.getCase("case_1")).rejects.toMatchObject({
      problem: {
        code: "GATEWAY_RESPONSE_INVALID",
        detail: "服务端返回了无法识别的错误响应，请联系系统管理员。"
      }
    });
  });
});
