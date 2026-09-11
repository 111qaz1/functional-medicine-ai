import { describe, expect, it } from "vitest";

import { buildReviewChanges, createReviewDraft } from "./review-diff";
import type { AnalysisResponse } from "./types";

const analysis: AnalysisResponse = {
  id: "analysis_1",
  case_id: "case_1",
  version: 1,
  revision: 3,
  status: "ready_for_review",
  progress: { current: 1, total: 1, percent: 100, current_file_name: null },
  case_summary: "虚构摘要",
  system_findings: [],
  abnormal_findings: [
    {
      id: "finding_1",
      name: "维生素 D",
      result_text: null,
      raw_value: "18",
      unit: "ng/mL",
      reference_range: "30-100",
      abnormal_flag: "low",
      interpretation: "内部保留字段",
      report_explanation: null,
      neutral_interpretation: null,
      support_need_text: null,
      source_file_id: "file_1",
      source_file_name: "fixture.txt",
      source_page: 1,
      source_text: "Vitamin D 18",
      confidence: 0.91,
      evidence_status: "verified",
      evidence_notes: ["不得回传"],
      observed_at: null
    },
    {
      id: "finding_2",
      name: "风险标记",
      result_text: "存在",
      raw_value: null,
      unit: null,
      reference_range: null,
      abnormal_flag: "genetic_risk",
      interpretation: null,
      report_explanation: null,
      neutral_interpretation: null,
      support_need_text: null,
      source_file_id: "file_1",
      source_file_name: "fixture.txt",
      source_page: 1,
      source_text: "Synthetic risk marker",
      confidence: 0.5,
      evidence_status: "needs_review",
      evidence_notes: [],
      observed_at: null
    }
  ],
  current_supplements: [
    { id: "supplement_1", name: "维生素 C", source_file_ids: [], source_file_names: [], doctor_added: false }
  ],
  food_sensitivity: {
    source_file_id: "questionnaire_1",
    source_file_name: "fixture-questionnaire.txt",
    source_page: 1,
    interpretations: [],
    valid: true,
    warning: null,
    items: [
      {
        id: "food_1",
        name: "牛奶",
        raw_value: "2",
        unit: null,
        abnormal_flag: "positive",
        severity: "moderate",
        reported_grade: "2",
        reported_grade_meaning: null,
        reference_range: null,
        grading_basis: null,
        source_page: 1,
        source_text: "Milk 2",
        evidence_status: "verified"
      }
    ]
  },
  warnings: [],
  error: null,
  draft_generation: { status: "idle", progress: 0, error: null },
  draft_id: null,
  created_at: "2026-08-22T00:00:00Z",
  updated_at: "2026-08-22T00:00:00Z"
};

describe("review delta builder", () => {
  it("supports an empty confirmation even when a response-only enum is not editable", () => {
    expect(buildReviewChanges(analysis, createReviewDraft(analysis))).toEqual({
      finding_changes: [],
      supplement_changes: [],
      food_sensitivity_changes: []
    });
  });

  it("emits only add, update, and remove instructions for changed public fields", () => {
    const draft = createReviewDraft(analysis);
    draft.findings[0] = { ...draft.findings[0], name: "25-羟维生素 D" };
    draft.findings = draft.findings.filter((item) => item.id !== "finding_2");
    draft.supplements = [];
    draft.foodSensitivityItems.push({
      id: "client_food_2",
      name: "鸡蛋",
      raw_value: "1",
      unit: null,
      abnormal_flag: "positive",
      severity: "mild",
      reported_grade: "1",
      reported_grade_meaning: null,
      reference_range: null,
      grading_basis: null,
      source_page: 1,
      source_text: "Egg 1",
      source_file_id: "questionnaire_1",
      source_file_name: "fixture-questionnaire.txt",
      is_new: true
    });

    const changes = buildReviewChanges(analysis, draft);
    expect(changes.finding_changes).toEqual([
      { op: "update", id: "finding_1", changes: { name: "25-羟维生素 D" } },
      { op: "remove", id: "finding_2" }
    ]);
    expect(changes.supplement_changes).toEqual([{ op: "remove", id: "supplement_1" }]);
    expect(changes.food_sensitivity_changes).toEqual([
      expect.objectContaining({ op: "add", value: expect.objectContaining({ name: "鸡蛋" }) })
    ]);
    expect(JSON.stringify(changes)).not.toContain("confidence");
    expect(JSON.stringify(changes)).not.toContain("evidence_notes");
    expect(JSON.stringify(changes)).not.toContain("interpretation");
  });
});
