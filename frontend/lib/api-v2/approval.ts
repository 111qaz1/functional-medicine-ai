import type { ApprovalRequest, DraftResponse } from "./types";
import { buildPublishableReport } from "./report-editor";

export interface ApprovalDraftState {
  excludedSkuIds: string[];
  dosageSelections: Record<string, string>;
  dosageNotes: Record<string, string>;
  publishableReport: string;
}

export function createApprovalDraft(draft: DraftResponse): ApprovalDraftState {
  return {
    excludedSkuIds: [],
    dosageSelections: Object.fromEntries(
      draft.recommended_skus.map((item) => [item.sku_id, item.dosage_option_id ?? ""])
    ),
    dosageNotes: {},
    publishableReport: buildPublishableReport(draft)
  };
}

export function buildApprovalRequest(draft: DraftResponse, state: ApprovalDraftState): ApprovalRequest {
  const knownSkuIds = new Set(draft.recommended_skus.map((item) => item.sku_id));
  const excludedSkuIds = [...new Set(state.excludedSkuIds)];
  if (excludedSkuIds.some((id) => !knownSkuIds.has(id))) throw new Error("排除列表包含未知 SKU。");
  if (draft.recommended_skus.length > 0 && excludedSkuIds.length >= draft.recommended_skus.length) {
    throw new Error("至少保留一项可发布推荐。");
  }

  const dosageOverrides = draft.recommended_skus.flatMap((item) => {
    if (excludedSkuIds.includes(item.sku_id)) return [];
    const optionId = state.dosageSelections[item.sku_id] ?? item.dosage_option_id ?? "";
    if (optionId === (item.dosage_option_id ?? "")) return [];
    if (!item.dosage_options.some((option) => option.option_id === optionId)) {
      throw new Error(`${item.display_name} 的剂量选项无效。`);
    }
    const note = state.dosageNotes[item.sku_id]?.trim() ?? "";
    if (!note) throw new Error(`${item.display_name} 改选非默认剂量时必须填写说明。`);
    return [{ sku_id: item.sku_id, option_id: optionId, note }];
  });

  const publishableReport = state.publishableReport.trim();
  if (!publishableReport) throw new Error("最终发布报告不能为空。");

  return {
    expected_revision: draft.revision,
    publishable_summary: publishableReport,
    excluded_sku_ids: excludedSkuIds,
    dosage_overrides: dosageOverrides
  };
}
