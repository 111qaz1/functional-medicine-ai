"use client";

import React, { useState } from "react";
import { DocumentArrowDownIcon, ShieldCheckIcon } from "@heroicons/react/24/outline";

import { MarkdownEditor, MarkdownPreview, type MarkdownViewMode } from "../markdown-editor";
import type { ApprovalDraftState } from "../../lib/api-v2/approval";
import type { DraftResponse, ReportResponse } from "../../lib/api-v2/types";
import { WorkflowNotice } from "./workflow-shell";

export function FinalReportEditor({ draft, value, report, reviewerName, busy, onChange, onApprove, onDownload, embedded = false }: {
  draft: DraftResponse;
  value: ApprovalDraftState;
  report: ReportResponse | null;
  reviewerName: string;
  busy: boolean;
  onChange(value: ApprovalDraftState): void;
  onApprove(): void;
  onDownload(): void;
  embedded?: boolean;
}) {
  const [mode, setMode] = useState<MarkdownViewMode>(embedded ? "edit" : "split");
  const approved = draft.status === "approved";
  const publishedText = report?.publishable_report ?? value.publishableReport;
  const includedCount = draft.recommended_skus.filter((item) => !value.excludedSkuIds.includes(item.sku_id)).length;

  return <div className="workflow-stack workflow-final-report">
    {approved ? <WorkflowNotice tone="success">最终报告已由 {report?.reviewer_id ?? reviewerName} 批准并锁定。</WorkflowNotice> : <WorkflowNotice tone="info">请确认完整患者报告内容；批准后将锁定正文并生成 PDF。</WorkflowNotice>}
    <div className="workflow-report-editor__header"><div><h3>最终发布内容</h3><p>三层健康画像、系统分析、四域生活方式方案、营养素方案和复查计划均在此编辑。</p></div></div>
    {approved ? <div className="workflow-report-readonly"><MarkdownPreview value={publishedText} /></div> : <MarkdownEditor value={value.publishableReport} onChange={(publishableReport) => onChange({ ...value, publishableReport })} mode={mode} onModeChange={setMode} />}
    <div className="workflow-approval-panel workflow-action-dock"><div className="workflow-reviewer-identity"><span>{approved ? "批准医生" : "当前批准医生"}</span><strong>{approved ? report?.reviewer_id ?? reviewerName : reviewerName}</strong><small>{approved && report ? new Date(report.approved_at).toLocaleString("zh-CN") : "批准身份由登录会话写入审计日志。"}</small></div><div className="workflow-action-row">{!approved ? <button className="workflow-button workflow-button--primary" type="button" disabled={busy || includedCount === 0 || !value.publishableReport.trim()} aria-busy={busy} onClick={onApprove}><ShieldCheckIcon className="workflow-button__icon" />{busy ? "正在发布…" : "审核发布并导出 PDF"}</button> : null}{approved ? <button className="workflow-button workflow-button--primary" type="button" disabled={busy} aria-busy={busy} onClick={onDownload}><DocumentArrowDownIcon className="workflow-button__icon" />{busy ? "正在下载…" : "下载 PDF"}</button> : null}<span>最终报告纳入 {includedCount} 项推荐</span></div></div>
  </div>;
}
