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
  it("marks the approval reviewer as required and exposes a local validation target", () => {
    const html = renderToStaticMarkup(
      <DraftApproval
        draft={draft}
        value={createApprovalDraft(draft)}
        onChange={() => undefined}
        busy={false}
        onApprove={() => undefined}
      />
    );

    expect(html).toContain("审批医生 ID（必填）");
    expect(html).toContain('required=""');
    expect(html).toContain('aria-describedby="approval-reviewer-error"');
    expect(html).toContain('id="approval-reviewer-error"');
  });
});
