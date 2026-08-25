"use client";

import React, { useEffect, useState } from "react";

import { MarkdownEditor, MarkdownPreview, type MarkdownViewMode } from "../markdown-editor";
import type { ApprovalDraftState } from "../../lib/api-v2/approval";
import type { DraftResponse, ReportResponse } from "../../lib/api-v2/types";
import { WorkflowNotice } from "./workflow-shell";

export function FinalReportEditor({
  draft,
  value,
  report,
  reviewerName,
  busy,
  onChange,
  onApprove,
  onDownload
}: {
  draft: DraftResponse;
  value: ApprovalDraftState;
  report: ReportResponse | null;
  reviewerName: string;
  busy: boolean;
  onChange(value: ApprovalDraftState): void;
  onApprove(): void;
  onDownload(): void;
}) {
  const [mode, setMode] = useState<MarkdownViewMode>("split");
  const [expanded, setExpanded] = useState(false);
  const approved = draft.status === "approved";
  const publishedText = report?.publishable_report ?? value.publishableReport;
  const includedCount = draft.recommended_skus.filter((item) => !value.excludedSkuIds.includes(item.sku_id)).length;

  useEffect(() => {
    if (!expanded) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setExpanded(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [expanded]);

  return (
    <div className="workflow-stack workflow-final-report">
      {approved ? (
        <WorkflowNotice tone="success">最终报告已由 {report?.reviewer_id ?? reviewerName} 批准并锁定。</WorkflowNotice>
      ) : (
        <WorkflowNotice tone="info">请确认完整患者报告内容；批准后将锁定正文并生成 PDF。</WorkflowNotice>
      )}

      <div className="workflow-report-editor__header">
        <div><h3>最终发布内容</h3><p>三层健康画像、系统分析、四域生活方式方案、营养素方案和复查计划均在此编辑。</p></div>
        {!approved ? <button className="workflow-button workflow-button--secondary" type="button" onClick={() => setExpanded(true)}>放大编辑</button> : null}
      </div>

      {approved ? (
        <div className="workflow-report-readonly"><MarkdownPreview value={publishedText} /></div>
      ) : (
        <MarkdownEditor value={value.publishableReport} onChange={(publishableReport) => onChange({ ...value, publishableReport })} mode={mode} onModeChange={setMode} />
      )}

      <div className="workflow-approval-panel">
        <div className="workflow-reviewer-identity">
          <span>{approved ? "批准医生" : "当前批准医生"}</span>
          <strong>{approved ? report?.reviewer_id ?? reviewerName : reviewerName}</strong>
          <small>{approved && report ? new Date(report.approved_at).toLocaleString("zh-CN") : "批准身份由登录会话写入审计日志。"}</small>
        </div>
        <div className="workflow-action-row">
          {!approved ? <button className="workflow-button workflow-button--primary" type="button" disabled={busy || includedCount === 0 || !value.publishableReport.trim()} onClick={onApprove}>{busy ? "正在发布…" : "审核发布并导出 PDF"}</button> : null}
          {approved ? <button className="workflow-button workflow-button--primary" type="button" disabled={busy} onClick={onDownload}>{busy ? "正在下载…" : "下载 PDF"}</button> : null}
          <span>最终报告纳入 {includedCount} 项推荐</span>
        </div>
      </div>

      {expanded && !approved ? (
        <div className="report-editor-overlay" role="presentation">
          <div className="report-editor-dialog" role="dialog" aria-modal="true" aria-labelledby="integration-report-editor-title">
            <div className="report-editor-dialog__head"><div><p className="section-card__eyebrow">Publishable report</p><h3 id="integration-report-editor-title">最终发布内容</h3></div><button className="workflow-button workflow-button--primary" type="button" onClick={() => setExpanded(false)}>完成</button></div>
            <MarkdownEditor value={value.publishableReport} onChange={(publishableReport) => onChange({ ...value, publishableReport })} mode={mode} onModeChange={setMode} expanded />
          </div>
        </div>
      ) : null}
    </div>
  );
}
