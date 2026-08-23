"use client";

import React from "react";

import type { ApprovalDraftState } from "../../lib/api-v2/approval";
import type { DraftResponse } from "../../lib/api-v2/types";
import { WorkflowNotice } from "./workflow-shell";

export interface DraftApprovalProps {
  draft: DraftResponse;
  value: ApprovalDraftState;
  reviewerName: string;
  onChange(value: ApprovalDraftState): void;
  busy: boolean;
  onApprove(): void;
}

function TextList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="workflow-text-list">
      <h3>{title}</h3>
      <ul>{items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</ul>
    </div>
  );
}

export function DraftApproval({ draft, value, reviewerName, onChange, busy, onApprove }: DraftApprovalProps) {
  const approved = draft.status === "approved";
  const includedCount = draft.recommended_skus.filter((item) => !value.excludedSkuIds.includes(item.sku_id)).length;

  return (
    <div className="workflow-stack">
      <div className="workflow-summary-grid">
        <TextList title="公开摘要" items={draft.public_summary} />
        <TextList title="重点指标" items={draft.key_lab_highlights} />
        <TextList title="生活方式建议" items={draft.lifestyle_actions} />
        <TextList title="推荐依据" items={draft.rationale} />
        <TextList title="证据说明" items={draft.evidence_details} />
        <TextList title="禁忌与注意" items={draft.contraindications} />
        <TextList title="缺失信息" items={draft.missing_info} />
        <TextList title="红旗提示" items={draft.red_flags} />
      </div>

      {draft.core_health_portrait ? (
        <section className="workflow-plan-block">
          <div className="workflow-plan-block__header"><h3>三层核心健康画像</h3><span>{draft.core_health_portrait.status}</span></div>
          <p className="workflow-prose">{draft.core_health_portrait.text}</p>
          {draft.core_health_portrait.decision.intervention_steps.length ? <ol>{draft.core_health_portrait.decision.intervention_steps.map((step) => <li key={step.order}><strong>{step.label}</strong></li>)}</ol> : null}
        </section>
      ) : null}

      {draft.structured_system_findings.length ? (
        <section className="workflow-plan-block">
          <div className="workflow-plan-block__header"><h3>功能医学系统优先级</h3><span>{draft.structured_system_findings.length} 个系统</span></div>
          <div className="workflow-system-grid">{draft.structured_system_findings.map((item) => <article key={item.system_id}><strong>{item.system_name}</strong><span>{item.priority_level} · {item.priority_score.toFixed(1)}</span><p>{item.summary}</p></article>)}</div>
        </section>
      ) : null}

      {draft.lifestyle_plan ? (
        <section className="workflow-plan-block">
          <div className="workflow-plan-block__header"><h3>四域生活方式方案</h3><span>{draft.lifestyle_plan.status}</span></div>
          <div className="workflow-lifestyle-grid">{draft.lifestyle_plan.sections.map((section) => <article key={section.domain}><h4>{section.title}</h4><ul>{section.actions.map((action) => <li key={action.action_id}>{action.text}{action.quantity ? `（${action.quantity}）` : ""}</li>)}</ul></article>)}</div>
          <TextList title="监测与复查" items={draft.lifestyle_plan.monitoring} />
        </section>
      ) : null}

      {draft.safety_decisions.length ? <section className="workflow-plan-block"><div className="workflow-plan-block__header"><h3>医生安全审核</h3><span>{draft.safety_decisions.length} 项</span></div><ul>{draft.safety_decisions.map((item) => <li key={`${item.rule_id}-${item.sku_id ?? "case"}`}><strong>{item.action}</strong>：{item.message}</li>)}</ul></section> : null}

      {draft.report_sections.length ? <details className="workflow-report-sections"><summary>查看规范化报告章节</summary>{draft.report_sections.map((section) => <TextList key={section.title} title={section.title} items={section.items} />)}</details> : null}

      {draft.manual_review_required ? (
        <WorkflowNotice tone="warning">该草案要求人工复核，发布前请逐项确认推荐、剂量和安全提示。</WorkflowNotice>
      ) : null}
      {draft.abstain_reason ? <WorkflowNotice tone="warning">{draft.abstain_reason}</WorkflowNotice> : null}

      <div className="workflow-recommendation-list">
        {draft.recommended_skus.map((item) => {
          const excluded = value.excludedSkuIds.includes(item.sku_id);
          const selectedOptionId = value.dosageSelections[item.sku_id] ?? item.dosage_option_id ?? "";
          const changed = selectedOptionId !== (item.dosage_option_id ?? "");
          return (
            <article className="workflow-recommendation" key={item.sku_id} data-state={excluded ? "excluded" : "included"}>
              <div className="workflow-recommendation__header">
                <div>
                  <h3>{item.display_name}</h3>
                  <p>{item.reason}</p>
                </div>
                <label className="workflow-check">
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
                  <span>纳入发布</span>
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
                      <option key={option.option_id} value={option.option_id}>
                        {option.label}：{option.display_text}
                      </option>
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
        {!draft.recommended_skus.length ? (
          <WorkflowNotice tone="error">草案没有可发布推荐，不能完成审批。</WorkflowNotice>
        ) : null}
      </div>

      <div className="workflow-approval-panel">
        <div className="workflow-reviewer-identity"><span>当前批准医生</span><strong>{reviewerName}</strong><small>批准身份由登录会话写入审计日志。</small></div>
        <label className="workflow-check">
          <input
            type="checkbox"
            checked={value.editSummary}
            disabled={busy || approved}
            onChange={(event) => onChange({ ...value, editSummary: event.target.checked })}
          />
          <span>主动覆盖公开摘要</span>
        </label>
        {value.editSummary ? (
          <label className="workflow-field">
            <span>公开摘要</span>
            <textarea
              rows={8}
              maxLength={50000}
              value={value.publishableSummary}
              disabled={busy || approved}
              onChange={(event) => onChange({ ...value, publishableSummary: event.target.value })}
            />
          </label>
        ) : (
          <p className="workflow-help">未开启覆盖时，审批请求发送 <code>publishable_summary: null</code>，沿用系统摘要。</p>
        )}
        <div className="workflow-action-row">
          <button
            className="workflow-button workflow-button--primary"
            type="button"
            disabled={busy || approved || includedCount === 0 || !draft.recommended_skus.length}
            onClick={onApprove}
          >
            {approved ? "已审批发布" : busy ? "正在发布…" : "审批并发布"}
          </button>
          <span>当前保留 {includedCount} 项推荐</span>
        </div>
      </div>
    </div>
  );
}
