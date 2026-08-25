"use client";

import React from "react";

import type { ApprovalDraftState } from "../../lib/api-v2/approval";
import type { DraftResponse } from "../../lib/api-v2/types";
import { WorkflowNotice } from "./workflow-shell";

export interface DraftApprovalProps {
  draft: DraftResponse;
  value: ApprovalDraftState;
  onChange(value: ApprovalDraftState): void;
  busy: boolean;
  onContinue(): void;
}

export function DraftApproval({ draft, value, onChange, busy, onContinue }: DraftApprovalProps) {
  const approved = draft.status === "approved";
  const included = draft.recommended_skus.filter((item) => !value.excludedSkuIds.includes(item.sku_id));
  const excludedDecisions = draft.safety_decisions.filter((item) => item.action === "exclude");

  return (
    <div className="workflow-stack">
      <div className="workflow-review-summary" aria-live="polite">
        <strong>{draft.recommended_skus.length} 项推荐</strong>
        <span>当前保留 {included.length} 项，排除 {draft.recommended_skus.length - included.length} 项</span>
      </div>

      {excludedDecisions.length ? (
        <details className="workflow-report-sections">
          <summary>规则排除记录（{excludedDecisions.length}）</summary>
          <ul>{excludedDecisions.map((item) => <li key={`${item.rule_id}-${item.sku_id ?? "case"}`}>{item.message}</li>)}</ul>
        </details>
      ) : null}

      {draft.structured_system_findings.length ? (
        <details className="workflow-report-sections">
          <summary>身体系统营养素覆盖（{draft.structured_system_findings.length}）</summary>
          <ul>
            {draft.structured_system_findings.map((finding) => {
              const uncovered = draft.uncovered_system_ids.includes(finding.system_id);
              const reason = draft.uncovered_system_reasons[finding.system_id];
              return (
                <li key={finding.system_id}>
                  <strong>{finding.system_name}：</strong>
                  {!uncovered ? "已由当前方案覆盖" : reason === "safety_excluded" ? "候选产品未通过安全校验" : reason === "no_approved_mapping" ? "暂无批准的产品映射" : "当前证据未达到推荐条件"}
                </li>
              );
            })}
          </ul>
        </details>
      ) : null}

      {draft.manual_review_required ? (
        <WorkflowNotice tone="warning">该方案要求人工复核，进入最终报告前请逐项确认推荐、剂量和安全提示。</WorkflowNotice>
      ) : null}
      {draft.abstain_reason ? <WorkflowNotice tone="warning">{draft.abstain_reason}</WorkflowNotice> : null}

      <div className="workflow-recommendation-list">
        {draft.recommended_skus.map((item) => {
          const excluded = value.excludedSkuIds.includes(item.sku_id);
          const selectedOptionId = value.dosageSelections[item.sku_id] ?? item.dosage_option_id ?? "";
          const changed = selectedOptionId !== (item.dosage_option_id ?? "");
          const reviewDecisions = draft.safety_decisions.filter((decision) => decision.sku_id === item.sku_id && decision.action === "requires_review");
          const warningDecisions = draft.safety_decisions.filter((decision) => decision.sku_id === item.sku_id && decision.action === "warn");
          return (
            <article className="workflow-recommendation" key={item.sku_id} data-state={excluded ? "excluded" : "included"}>
              <div className="workflow-recommendation__header">
                <div>
                  <h3>{item.display_name}</h3>
                  <p>{item.reason}</p>
                  {reviewDecisions.length ? <span className="workflow-status-badge">需要医生确认</span> : null}
                  {warningDecisions.length ? <span className="workflow-status-badge">注意事项</span> : null}
                </div>
                <label className="workflow-check workflow-inclusion-control">
                  <input
                    type="checkbox"
                    checked={!excluded}
                    disabled={busy || approved}
                    onChange={(event) => onChange({
                      ...value,
                      excludedSkuIds: event.target.checked
                        ? value.excludedSkuIds.filter((id) => id !== item.sku_id)
                        : [...value.excludedSkuIds, item.sku_id]
                    })}
                  />
                  <span>纳入报告</span>
                </label>
              </div>
              <div className="workflow-form-grid">
                <label className="workflow-field">
                  <span>批准剂量</span>
                  <select
                    value={selectedOptionId}
                    disabled={busy || approved || excluded}
                    onChange={(event) => onChange({
                      ...value,
                      dosageSelections: { ...value.dosageSelections, [item.sku_id]: event.target.value }
                    })}
                  >
                    {item.dosage_options.map((option) => (
                      <option key={option.option_id} value={option.option_id}>{option.label}：{option.display_text}</option>
                    ))}
                  </select>
                </label>
                {changed && !excluded ? (
                  <label className="workflow-field">
                    <span>调整说明（必填）</span>
                    <input
                      value={value.dosageNotes[item.sku_id] ?? ""}
                      disabled={busy || approved}
                      onChange={(event) => onChange({
                        ...value,
                        dosageNotes: { ...value.dosageNotes, [item.sku_id]: event.target.value }
                      })}
                    />
                  </label>
                ) : null}
              </div>
              <div className="workflow-recommendation__details">
                {item.evidence_details.length ? <p><strong>证据：</strong>{item.evidence_details.join("；")}</p> : null}
                {item.dosage_match_reasons.length ? <p><strong>剂量依据：</strong>{item.dosage_match_reasons.join("；")}</p> : null}
                {item.warnings.length ? <p className="workflow-danger-text"><strong>提示：</strong>{item.warnings.join("；")}</p> : null}
                {item.current_supplement_overlap_notice ? <p className="workflow-danger-text"><strong>当前补充剂重叠：</strong>{item.current_supplement_overlap_notice}</p> : null}
              </div>
            </article>
          );
        })}
        {!draft.recommended_skus.length ? <WorkflowNotice tone="error">方案没有可发布推荐，不能进入最终报告。</WorkflowNotice> : null}
      </div>

      <div className="workflow-action-row workflow-action-row--sticky">
        <button
          className="workflow-button workflow-button--primary"
          type="button"
          disabled={busy || included.length === 0 || !draft.recommended_skus.length}
          onClick={onContinue}
        >
          {approved ? "查看最终报告" : "继续编辑最终报告"}
        </button>
        <span>当前保留 {included.length} 项推荐</span>
      </div>
    </div>
  );
}
