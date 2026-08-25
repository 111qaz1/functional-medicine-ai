import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { createApprovalDraft } from "../../lib/api-v2/approval";
import type { DraftResponse } from "../../lib/api-v2/types";
import { DraftApproval } from "./draft-approval";

const draft = {
  id: "draft_fixture",
  status: "pending_review",
  public_summary: [],
  key_lab_highlights: [],
  lifestyle_actions: [],
  rationale: [],
  evidence_details: [],
  contraindications: [],
  missing_info: [],
  red_flags: [],
  manual_review_required: false,
  abstain_reason: null,
  core_health_portrait: null,
  structured_system_findings: [],
  lifestyle_plan: null,
  safety_decisions: [],
  uncovered_system_ids: [],
  uncovered_system_reasons: {},
  report_sections: [],
  recommended_skus: [
    {
      sku_id: "SKU_FIXTURE",
      display_name: "虚构产品",
      reason: "仅用于组件测试",
      dosage_option_id: "default",
      dosage_options: [
        {
          option_id: "default",
          label: "标准档",
          display_text: "每日 1 粒",
          requires_review: false
        }
      ],
      evidence_details: [],
      dosage_match_reasons: [],
      warnings: [],
      current_supplement_overlap_notice: null
    }
  ]
} as unknown as DraftResponse;

describe("DraftApproval", () => {
  it("keeps step five focused on recommendation adjustments", () => {
    const html = renderToStaticMarkup(
      <DraftApproval
        draft={draft}
        value={createApprovalDraft(draft)}
        onChange={() => undefined}
        busy={false}
        onContinue={() => undefined}
      />
    );

    expect(html).toContain("继续编辑最终报告");
    expect(html).toContain("虚构产品");
    expect(html).not.toContain("三层核心健康画像");
  });
});
