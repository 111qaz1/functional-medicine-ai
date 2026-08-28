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
  revision: 1,
  status: "pending_review",
  public_summary: ["虚构公开摘要"],
  report_sections: [
    {
      title: "核心结论与健康画像",
      items: ["虚构报告正文"]
    }
  ],
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
  it("submits exclusions, changed dosage options, and the full publishable report", () => {
    const state = createApprovalDraft(draft);
    state.excludedSkuIds = ["SKU_2"];
    state.dosageSelections.SKU_1 = "alternate";
    state.dosageNotes.SKU_1 = "根据虚构复核条件调整";

    const request = buildApprovalRequest(draft, state);
    expect(request).toMatchObject({
      expected_revision: draft.revision,
      excluded_sku_ids: ["SKU_2"],
      dosage_overrides: [
        { sku_id: "SKU_1", option_id: "alternate", note: "根据虚构复核条件调整" }
      ]
    });
    expect(request.publishable_summary).toContain("功能医学综合分析与首月干预方案");
  });

  it("rejects excluding every recommendation", () => {
    const state = createApprovalDraft(draft);
    state.excludedSkuIds = ["SKU_1", "SKU_2"];
    expect(() => buildApprovalRequest(draft, state)).toThrow("至少保留一项");
  });

  it("requires a note for a non-default dosage", () => {
    const state = createApprovalDraft(draft);
    state.dosageSelections.SKU_1 = "alternate";
    expect(() => buildApprovalRequest(draft, state)).toThrow("必须填写说明");
  });
});
