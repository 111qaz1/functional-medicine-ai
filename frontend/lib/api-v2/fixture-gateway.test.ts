import { afterEach, describe, expect, it, vi } from "vitest";

import { createMemoryFixtureStorage, FixtureWorkflowGateway, type FixtureScenario } from "./fixture-gateway";

async function createCaseWithAttachment(gateway: FixtureWorkflowGateway) {
  const created = await gateway.createCase({
    customer_name: "虚构测试用户",
    consultant_id: "fixture-consultant",
    notes: "无真实患者信息"
  });
  await gateway.uploadAttachments(created.id, "medical_record", [
    new File(["25-OH Vitamin D: 18 ng/mL"], "fixture-medical-record.txt", { type: "text/plain" })
  ]);
  await gateway.uploadAttachments(created.id, "questionnaire", [
    new File(["Milk: grade 2"], "fixture-questionnaire.txt", { type: "text/plain" })
  ]);
  return created;
}

async function completeAnalysis(gateway: FixtureWorkflowGateway, caseId: string) {
  const accepted = await gateway.startAnalysis(caseId, { third_party_processing_confirmed: true });
  await gateway.getOperation(accepted.operation.operation_id);
  const terminal = await gateway.getOperation(accepted.operation.operation_id);
  return { accepted, terminal, analysis: await gateway.getLatestAnalysis(caseId) };
}

async function prepareDraft(gateway: FixtureWorkflowGateway) {
  const created = await createCaseWithAttachment(gateway);
  const { analysis } = await completeAnalysis(gateway, created.id);
  const accepted = await gateway.submitReview(created.id, analysis.id, {
    reviewer_id: "fixture-doctor",
    expected_revision: analysis.revision,
    finding_changes: [],
    supplement_changes: [],
    food_sensitivity_changes: []
  });
  await gateway.getOperation(accepted.operation.operation_id);
  const terminal = await gateway.getOperation(accepted.operation.operation_id);
  const latest = await gateway.getLatestAnalysis(created.id);
  return { created, analysis: latest, terminal, draft: latest.draft_id ? await gateway.getDraft(latest.draft_id) : null };
}

function gateway(scenario: FixtureScenario = "success") {
  return new FixtureWorkflowGateway(scenario, createMemoryFixtureStorage());
}

describe("FixtureWorkflowGateway", () => {
  afterEach(() => vi.restoreAllMocks());

  it("runs the complete case-to-PDF flow without making a network request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network must not be used"));
    const fixture = gateway();
    const prepared = await prepareDraft(fixture);

    expect(prepared.terminal.status).toBe("succeeded");
    expect(prepared.draft?.recommended_skus.length).toBeGreaterThan(0);
    const approved = await fixture.approveDraft(prepared.draft!.id, {
      reviewer_id: "fixture-doctor",
      publishable_summary: null,
      excluded_sku_ids: ["SKU-FIXTURE-MULTI"],
      dosage_overrides: [
        { sku_id: "SKU-FIXTURE-D3", option_id: "alternate", note: "Fixture 剂量调整说明" }
      ]
    });
    const report = await fixture.getReport(prepared.draft!.id);
    const pdf = await fixture.downloadReport(prepared.draft!.id);
    const publishedDraft = await fixture.getDraft(prepared.draft!.id);

    expect(approved.status).toBe("approved");
    expect(publishedDraft.status).toBe("approved");
    expect(publishedDraft.recommended_skus).toHaveLength(1);
    expect(publishedDraft.recommended_skus[0]).toMatchObject({
      sku_id: "SKU-FIXTURE-D3",
      dosage: "隔日 1 粒",
      dosage_option_id: "alternate",
      dosage_option_label: "备选档"
    });
    expect(report.status).toBe("ready");
    expect(pdf.filename).toBe(report.filename);
    expect(await pdf.blob.text()).toContain("%PDF-1.4");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("persists doctor review deltas before generating the draft", async () => {
    const fixture = gateway();
    const created = await createCaseWithAttachment(fixture);
    const { analysis } = await completeAnalysis(fixture, created.id);

    await fixture.submitReview(created.id, analysis.id, {
      reviewer_id: "fixture-doctor",
      expected_revision: analysis.revision,
      finding_changes: [
        { op: "update", id: "finding_fixture_vitamin_d", changes: { name: "25-羟维生素 D（医生已修订）" } },
        {
          op: "add",
          value: {
            name: "虚构新增指标",
            result_text: "阳性",
            raw_value: null,
            unit: null,
            reference_range: null,
            abnormal_flag: "positive",
            source_file_id: "file_fixture_medical",
            source_file_name: "fixture-medical-record.txt",
            source_page: 1,
            source_text: "Synthetic doctor-added evidence"
          }
        }
      ],
      supplement_changes: [{ op: "remove", id: "supplement_fixture_c" }],
      food_sensitivity_changes: [
        { op: "update", id: "food_fixture_milk", changes: { severity: "high" } }
      ]
    });

    const reviewed = await fixture.getLatestAnalysis(created.id);
    expect(reviewed.revision).toBe(analysis.revision + 1);
    expect(reviewed.abnormal_findings.map((item) => item.name)).toEqual([
      "25-羟维生素 D（医生已修订）",
      "虚构新增指标"
    ]);
    expect(reviewed.current_supplements).toEqual([]);
    expect(reviewed.food_sensitivity?.items[0].severity).toBe("high");
  });

  it("uses content identity for medical duplicates and never cross-deduplicates questionnaires", async () => {
    const fixture = gateway();
    const created = await fixture.createCase({ customer_name: "虚构用户", consultant_id: null, notes: null });

    const first = await fixture.uploadAttachments(created.id, "medical_record", [
      new File(["same-content"], "first-name.txt", { type: "text/plain" })
    ]);
    const renamedDuplicate = await fixture.uploadAttachments(created.id, "medical_record", [
      new File(["same-content"], "renamed.txt", { type: "text/plain" })
    ]);
    const sameNameDifferentContent = await fixture.uploadAttachments(created.id, "medical_record", [
      new File(["different-content"], "first-name.txt", { type: "text/plain" })
    ]);
    const questionnaire = await fixture.uploadAttachments(created.id, "questionnaire", [
      new File(["same-content"], "first-name.txt", { type: "text/plain" })
    ]);

    expect(first.items[0].status).toBe("parsed");
    expect(renamedDuplicate.items[0].status).toBe("duplicate");
    expect(sameNameDifferentContent.items[0].status).toBe("parsed");
    expect(questionnaire.items[0].status).toBe("questionnaire_imported");
  });

  it("returns ordered partial attachment failures without rolling back accepted files", async () => {
    const fixture = gateway("attachment_partial_failure");
    const created = await fixture.createCase({ customer_name: "虚构用户", consultant_id: null, notes: null });
    const result = await fixture.uploadAttachments(created.id, "medical_record", [
      new File(["ok"], "accepted.txt", { type: "text/plain" }),
      new File(["bad"], "rejected.txt", { type: "text/plain" })
    ]);
    expect(result.items.map((item) => item.status)).toEqual(["parsed", "failed"]);
    expect(result.meta).toMatchObject({ accepted_count: 1, failed_count: 1 });
    expect((await fixture.getCase(created.id)).attachments).toHaveLength(1);
  });

  it("projects an analysis execution failure as a failed operation", async () => {
    const fixture = gateway("analysis_failure");
    const created = await createCaseWithAttachment(fixture);
    const { terminal } = await completeAnalysis(fixture, created.id);
    expect(terminal).toMatchObject({
      status: "failed",
      failure: { code: "ANALYSIS_FAILED", retryable: true }
    });
  });

  it("supports retrying a failed draft generation", async () => {
    const fixture = gateway("draft_generation_failure");
    const prepared = await prepareDraft(fixture);
    expect(prepared.terminal.status).toBe("failed");
    expect(prepared.analysis.draft_generation.status).toBe("failed");

    const retry = await fixture.retryDraftGeneration(prepared.created.id, prepared.analysis.id);
    await fixture.getOperation(retry.operation.operation_id);
    expect((await fixture.getOperation(retry.operation.operation_id)).status).toBe("succeeded");
    expect((await fixture.getLatestAnalysis(prepared.created.id)).draft_id).toBeTruthy();
  });

  it("simulates a revision conflict before saving review changes", async () => {
    const fixture = gateway("revision_conflict");
    const created = await createCaseWithAttachment(fixture);
    const { analysis } = await completeAnalysis(fixture, created.id);
    await expect(fixture.submitReview(created.id, analysis.id, {
      reviewer_id: "fixture-doctor",
      expected_revision: analysis.revision,
      finding_changes: [],
      supplement_changes: [],
      food_sensitivity_changes: []
    })).rejects.toMatchObject({ problem: { code: "ANALYSIS_REVISION_CONFLICT", status: 409 } });
  });

  it("simulates approval validation and report-not-ready errors", async () => {
    const invalidGateway = gateway("approval_validation_error");
    const invalidApproval = await prepareDraft(invalidGateway);
    await expect(invalidGateway.approveDraft(invalidApproval.draft!.id, {
      reviewer_id: "fixture-doctor",
      publishable_summary: null,
      excluded_sku_ids: [],
      dosage_overrides: []
    })).rejects.toMatchObject({ problem: { code: "REQUEST_VALIDATION_FAILED", status: 422 } });

    const notReadyGateway = gateway("report_not_ready");
    const prepared = await prepareDraft(notReadyGateway);
    await notReadyGateway.approveDraft(prepared.draft!.id, {
      reviewer_id: "fixture-doctor",
      publishable_summary: null,
      excluded_sku_ids: [],
      dosage_overrides: []
    });
    await expect(notReadyGateway.getReport(prepared.draft!.id)).rejects.toMatchObject({
      problem: { code: "REPORT_NOT_READY", status: 409 }
    });
  });

  it("simulates authentication failure before returning any fixture data", async () => {
    const fixture = gateway("authentication_failure");
    await expect(fixture.createCase({ customer_name: "虚构用户", consultant_id: null, notes: null })).rejects.toMatchObject({
      problem: { code: "AUTHENTICATION_REQUIRED", status: 401 }
    });
  });
});
