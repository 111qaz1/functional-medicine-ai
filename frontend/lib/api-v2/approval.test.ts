import { describe, expect, it } from "vitest";

import { buildApprovalRequest, createApprovalDraft } from "./approval";
import type { DraftResponse } from "./types";

const regimen = {
  unit: "capsule",
  single_dose_min: 1,
  single_dose_max: 1,
  daily_frequency_min: 1,
  daily_frequency_max: 1,
  weekly_frequency_min: null,
  weekly_frequency_max: null,
  timing: [],
  interval_hours_min: null,
  interval_hours_max: null,
  daily_max: 1,
  duration: null,
  maintenance: null
};

const draft = {
  id: "draft_1",
  status: "pending_review",
  public_summary: ["虚构公开摘要"],
  recommended_skus: [
    {
      sku_id: "SKU_1",
      display_name: "虚构产品一",
      dosage_option_id: "default",
      dosage_options: [
        { option_id: "default", label: "默认", display_text: "每日 1 粒", requires_review: false, regimen },
        { option_id: "alternate", label: "备选", display_text: "隔日 1 粒", requires_review: true, regimen }
      ]
    },
    {
      sku_id: "SKU_2",
      display_name: "虚构产品二",
      dosage_option_id: "default",
      dosage_options: [
        { option_id: "default", label: "默认", display_text: "每日 1 粒", requires_review: false, regimen }
      ]
    }
  ]
} as unknown as DraftResponse;

describe("approval payload builder", () => {
  it("submits only exclusions and changed dosage options, leaving summary untouched by default", () => {
    const state = createApprovalDraft(draft);
    state.reviewerId = "doctor_1";
    state.excludedSkuIds = ["SKU_2"];
    state.dosageSelections.SKU_1 = "alternate";
    state.dosageNotes.SKU_1 = "根据虚构复核条件调整";

    expect(buildApprovalRequest(draft, state)).toEqual({
      reviewer_id: "doctor_1",
      publishable_summary: null,
      excluded_sku_ids: ["SKU_2"],
      dosage_overrides: [
        { sku_id: "SKU_1", option_id: "alternate", note: "根据虚构复核条件调整" }
      ]
    });
  });

  it("rejects excluding every recommendation", () => {
    const state = createApprovalDraft(draft);
    state.reviewerId = "doctor_1";
    state.excludedSkuIds = ["SKU_1", "SKU_2"];
    expect(() => buildApprovalRequest(draft, state)).toThrow("至少保留一项");
  });

  it("requires a note for a non-default dosage", () => {
    const state = createApprovalDraft(draft);
    state.reviewerId = "doctor_1";
    state.dosageSelections.SKU_1 = "alternate";
    expect(() => buildApprovalRequest(draft, state)).toThrow("必须填写说明");
  });
});
