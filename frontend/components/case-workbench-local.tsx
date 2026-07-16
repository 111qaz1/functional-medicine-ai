"use client";

import Link from "next/link";
import { ChangeEvent, useEffect, useMemo, useState } from "react";

import {
  approveDraft,
  deleteCaseFile,
  fetchCase,
  fetchCurrentUser,
  fetchLatestCaseAnalysis,
  getPdfReportUrl,
  reviewAndGenerate,
  startCaseAnalysis,
  updateClinicalSummary,
  uploadCaseFile
} from "../lib/api";
import {
  AbnormalFinding,
  CaseAnalysis,
  CaseDetailResponse,
  RecommendationDraft
} from "../lib/types";
import { SectionCard } from "./section-card";
import { StatusPillLocal } from "./status-pill-local";

const ACTIVE_ANALYSIS = new Set(["queued", "preparing", "analyzing_documents", "synthesizing", "validating"]);

const ANALYSIS_LABELS: Record<string, string> = {
  queued: "排队中",
  preparing: "准备资料",
  analyzing_documents: "逐份分析资料",
  synthesizing: "病例级综合",
  validating: "证据校验",
  ready_for_review: "待医生校对",
  reviewed: "校对已保存",
  stale: "资料已变化，需重新分析",
  failed: "分析失败"
};

function reportText(draft: RecommendationDraft | null | undefined) {
  if (!draft) return "";
  return Object.entries(draft.report_sections)
    .map(([title, raw]) => {
      const items = Array.isArray(raw) ? raw : [raw];
      return [`## ${title}`, ...items.filter(Boolean).map((item) => `- ${item}`)].join("\n");
    })
    .join("\n\n");
}

function cloneFindings(items: AbnormalFinding[]) {
  return items.map((item) => ({ ...item, evidence_notes: [...item.evidence_notes] }));
}

function evidenceLabel(status: AbnormalFinding["evidence_status"]) {
  if (status === "verified_text") return "文本证据已核对";
  if (status === "visual_model_only") return "扫描件：仅模型视觉识别";
  return "需医生确认";
}

function abnormalFlagLabel(flag: string) {
  if (flag === "high") return "偏高";
  if (flag === "low") return "偏低";
  if (flag === "positive") return "阳性/存在";
  return "异常";
}

function findingResultLabel(finding: AbnormalFinding) {
  return finding.result_text?.trim()
    || [finding.raw_value, finding.unit].filter(Boolean).join(" ")
    || finding.interpretation?.trim()
    || "已发现异常";
}

export function CaseWorkbenchLocal({ caseId }: { caseId: string }) {
  const [payload, setPayload] = useState<CaseDetailResponse | null>(null);
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [findings, setFindings] = useState<AbnormalFinding[]>([]);
  const [reviewerId, setReviewerId] = useState("reviewer-01");
  const [clinicalSummary, setClinicalSummary] = useState("");
  const [publishableSummary, setPublishableSummary] = useState("");
  const [publishableEditorExpanded, setPublishableEditorExpanded] = useState(false);
  const [excludedSkuIds, setExcludedSkuIds] = useState<string[]>([]);
  const [thirdPartyConfirmed, setThirdPartyConfirmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reviewActionError, setReviewActionError] = useState<string | null>(null);
  const [reviewActionNotice, setReviewActionNotice] = useState<string | null>(null);

  async function loadCase() {
    const next = await fetchCase(caseId);
    setPayload(next);
    setClinicalSummary(next.case.clinical_summary_text ?? "");
    setPublishableSummary(reportText(next.latest_draft));
    return next;
  }

  async function loadLatestAnalysis(options?: { quiet404?: boolean }) {
    try {
      const next = await fetchLatestCaseAnalysis(caseId);
      setAnalysis(next);
      const source = next.reviewed_abnormal_findings.length
        ? next.reviewed_abnormal_findings
        : next.abnormal_findings;
      setFindings(cloneFindings(source));
      return next;
    } catch (err) {
      if (!options?.quiet404) throw err;
      setAnalysis(null);
      setFindings([]);
      return null;
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function boot() {
      try {
        setLoading(true);
        const [nextCase] = await Promise.all([loadCase(), loadLatestAnalysis({ quiet404: true })]);
        if (!cancelled) setClinicalSummary(nextCase.case.clinical_summary_text ?? "");
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "加载病例失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void boot();
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  useEffect(() => {
    void fetchCurrentUser()
      .then((response) => {
        if (response.doctor) setReviewerId(response.doctor.display_name || response.doctor.username);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!publishableEditorExpanded) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setPublishableEditorExpanded(false);
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [publishableEditorExpanded]);

  useEffect(() => {
    if (!analysis || !ACTIVE_ANALYSIS.has(analysis.status)) return;
    const timer = window.setInterval(() => {
      void loadLatestAnalysis()
        .then((next) => {
          if (next && !ACTIVE_ANALYSIS.has(next.status)) void loadCase();
        })
        .catch((err) => setError(err instanceof Error ? err.message : "读取分析进度失败"));
    }, 1800);
    return () => window.clearInterval(timer);
  }, [analysis?.id, analysis?.status]);

  const validFiles = useMemo(
    () => payload?.case.files.filter((file) => file.intake_status !== "invalid") ?? [],
    [payload]
  );

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    if (!files.length) return;
    try {
      setBusy(true);
      setNotice(`正在对 ${files.length} 份资料执行格式、页数、哈希和文本层预检。`);
      for (const file of files) await uploadCaseFile(caseId, file);
      await loadCase();
      await loadLatestAnalysis({ quiet404: true });
      setNotice("资料已上传。上传阶段未调用大模型，也未生成任何指标或草案。");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  async function handleDeleteFile(fileId: string) {
    try {
      setBusy(true);
      await deleteCaseFile(caseId, fileId);
      await loadCase();
      await loadLatestAnalysis({ quiet404: true });
      setNotice("文件已删除；旧分析和未发布草案已失效。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除文件失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveSummary() {
    try {
      setBusy(true);
      await updateClinicalSummary(caseId, clinicalSummary.trim());
      await loadCase();
      await loadLatestAnalysis({ quiet404: true });
      setNotice("医生填写的病例总结已单独保存；如已有分析，该分析现已失效。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存病例总结失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleStartAnalysis() {
    try {
      setBusy(true);
      const next = await startCaseAnalysis(caseId, thirdPartyConfirmed);
      setAnalysis(next);
      setFindings([]);
      setNotice("综合分析任务已创建，可留在页面查看逐文件进度。");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法开始综合分析");
    } finally {
      setBusy(false);
    }
  }

  function updateFinding(index: number, patch: Partial<AbnormalFinding>) {
    setFindings((current) => current.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)));
  }

  function addFinding() {
    const firstFile = validFiles[0];
    if (!firstFile) return;
    setFindings((current) => [
      ...current,
      {
        id: `manual_${Date.now()}`,
        name: "",
        result_text: "",
        raw_value: null,
        unit: null,
        reference_range: null,
        abnormal_flag: "unknown",
        interpretation: "",
        source_file_id: firstFile.id,
        source_file_name: firstFile.filename,
        source_page: 1,
        source_text: "医生人工补充",
        confidence: 1,
        evidence_status: "needs_review",
        evidence_notes: ["医生人工补充。"]
      }
    ]);
  }

  async function handleReviewAndGenerate() {
    if (!analysis) return;
    if (findings.some((item) => !item.name.trim() || !item.source_file_id || item.source_page < 1)) {
      const message = "每条异常都需要名称、来源文件和有效页码。";
      setError(message);
      setReviewActionError(message);
      setReviewActionNotice(null);
      return;
    }
    try {
      setBusy(true);
      setReviewActionError(null);
      setReviewActionNotice("正在保存医生校对并生成营养素草案，请稍候……");
      const result = await reviewAndGenerate(
        caseId,
        analysis.id,
        reviewerId,
        analysis.revision,
        findings
      );
      setAnalysis(result.analysis);
      if (result.draft_generated) {
        await loadCase();
        const message = "异常校对已保存，营养素草案已生成并进入待审核状态。";
        setNotice(message);
        setReviewActionNotice(message);
      } else {
        const message = `校对已保存，草案生成失败：${result.generation_error ?? "未知错误"}。可直接重试，不会重新读取 PDF。`;
        setNotice(message);
        setReviewActionError(message);
        setReviewActionNotice(null);
      }
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "保存校对并生成草案失败";
      setError(message);
      setReviewActionError(message);
      setReviewActionNotice(null);
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove() {
    const draft = payload?.latest_draft;
    if (!draft) return;
    const includedRecommendations = draft.recommended_skus.filter((item) => !excludedSkuIds.includes(item.sku_id));
    if (!includedRecommendations.length) {
      setError("至少保留一项营养素推荐后才能审核发布。");
      return;
    }
    try {
      setBusy(true);
      await approveDraft(draft.id, reviewerId, publishableSummary, { excluded_sku_ids: excludedSkuIds });
      await loadCase();
      setNotice("报告已审核发布，PDF 已可下载。");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "审核发布失败");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="muted">正在加载病例工作台...</p>;
  if (!payload) return <p className="error-text">{error ?? "病例工作台加载失败"}</p>;

  const caseRecord = payload.case;
  const latestDraft = payload.latest_draft;
  const canStart = validFiles.length > 0 && (!analysis || ["failed", "stale"].includes(analysis.status));
  const linkedDraftNeedsRegeneration = Boolean(
    analysis?.draft_id &&
    latestDraft?.id === analysis.draft_id &&
    latestDraft.recommended_skus.length === 0
  );
  const canReview = analysis &&
    ["ready_for_review", "reviewed"].includes(analysis.status) &&
    (!analysis.draft_id || linkedDraftNeedsRegeneration);
  const includedRecommendationCount = latestDraft
    ? latestDraft.recommended_skus.filter((item) => !excludedSkuIds.includes(item.sku_id)).length
    : 0;

  return (
    <div className="workbench">
      <div className="workbench__hero">
        <div>
          <Link href="/" className="back-link">返回工作台</Link>
          <h1>{caseRecord.customer_name}</h1>
          <p className="muted">
            病例 ID {caseRecord.id} · 顾问 {caseRecord.consultant_id ?? "未分配"} · 最近更新 {new Date(caseRecord.updated_at).toLocaleString("zh-CN")}
          </p>
        </div>
        <StatusPillLocal status={caseRecord.status} />
      </div>

      {error ? <p className="error-text">{error}</p> : null}
      {notice ? <div className="info-note"><strong>提示</strong><p className="muted">{notice}</p></div> : null}

      <div className="workbench-grid">
        <SectionCard title="统一资料上传" subtitle="Unified intake">
          <label className="upload-dropzone">
            <input
              type="file"
              multiple
              accept=".pdf,.docx,.pptx,.txt,.md,.csv,.json,.png,.jpg,.jpeg,.bmp,.gif,.tif,.tiff,.webp"
              onChange={handleUpload}
              disabled={busy}
            />
            <span>上传病例报告、MSQ、肠道报告、慢性食物敏感报告或总结截图</span>
            <small>仅做轻量预检；明显无关文件会提示但不会阻止上传。默认单文件 50 MB、PDF 200 页。</small>
          </label>
          <div className="stack">
            {caseRecord.files.map((file) => (
              <div className="file-row" key={file.id}>
                <div>
                  <strong>{file.filename}</strong>
                  <p className="muted">
                    {Math.round(file.size_bytes / 1024)} KB · {file.page_count || "-"} 页 · {file.is_scanned ? "扫描/图片资料" : "含文本层"} · 状态 {file.intake_status}
                  </p>
                  {file.precheck_warning ? <p className="error-text">{file.precheck_warning}</p> : null}
                  {file.validation_error ? <p className="error-text">{file.validation_error}</p> : null}
                </div>
                <button type="button" className="secondary-button secondary-button--danger" disabled={busy} onClick={() => void handleDeleteFile(file.id)}>
                  删除
                </button>
              </div>
            ))}
            {!caseRecord.files.length ? <p className="muted">尚未上传资料。</p> : null}
          </div>

          <div className="section-divider">
            <strong>医生事先填写的病例总结</strong>
            <p className="muted">该原文与模型病例总结分开保存，模型不会覆盖它。</p>
          </div>
          <label className="field">
            <span>clinical_summary_text</span>
            <textarea rows={7} value={clinicalSummary} onChange={(event) => setClinicalSummary(event.target.value)} />
          </label>
          <button type="button" className="secondary-button" disabled={busy} onClick={() => void handleSaveSummary()}>保存医生病例总结</button>

          <div className="section-divider">
            <strong>开始综合分析</strong>
            <p className="muted">确认资料后才调用大模型。第一次分析不会生成 SKU、剂量或疗程。</p>
          </div>
          <label className="file-row">
            <span><input type="checkbox" checked={thirdPartyConfirmed} onChange={(event) => setThirdPartyConfirmed(event.target.checked)} /> 已确认获得将本病例资料发送至 Doubao 第三方模型处理的授权</span>
          </label>
          <button type="button" className="primary-button" disabled={busy || !canStart || !thirdPartyConfirmed} onClick={() => void handleStartAnalysis()}>
            {analysis?.status === "failed" || analysis?.status === "stale" ? "重新开始综合分析" : "确认资料并开始综合分析"}
          </button>
        </SectionCard>

        <SectionCard title="综合分析进度" subtitle="Asynchronous analysis">
          {!analysis ? <p className="muted">确认资料后，这里会显示逐文件处理进度。</p> : (
            <div className="stack">
              <div className="file-row">
                <div>
                  <strong>{ANALYSIS_LABELS[analysis.status] ?? analysis.status}</strong>
                  <p className="muted">分析版本 {analysis.version} · 模型 {analysis.model_version}</p>
                </div>
                <span className="indicator-status indicator-status--info">{analysis.progress_current}/{analysis.progress_total}</span>
              </div>
              {analysis.current_file_name ? <p className="muted">正在处理：{analysis.current_file_name}</p> : null}
              {analysis.error_message ? <p className="error-text">{analysis.error_code}: {analysis.error_message}</p> : null}
              {analysis.warnings.map((warning, index) => <p className="muted" key={`${warning}-${index}`}>⚠ {warning}</p>)}
              {analysis.ignored_files.length ? <p className="muted">模型判断已忽略：{analysis.ignored_files.join("、")}</p> : null}
            </div>
          )}
        </SectionCard>

        {analysis && ["ready_for_review", "reviewed"].includes(analysis.status) ? (
          <>
            <SectionCard title="初步病例综合" subtitle="Model synthesis">
              <div className="stack">
                <div><strong>病例总结</strong><p className="case-synthesis-text">{analysis.reviewed_case_summary ?? analysis.case_summary ?? "暂无"}</p></div>
                <div><strong>功能医学系统发现</strong>
                  <ul>{(analysis.reviewed_system_findings.length ? analysis.reviewed_system_findings : analysis.system_findings).map((item) => <li key={item}>{item}</li>)}</ul>
                </div>
              </div>
            </SectionCard>

            {analysis.questionnaire ? (
              <SectionCard title="MSQ 摘要" subtitle="Read-only questionnaire">
                <div className="grid-two">
                  <div><strong>主要诉求</strong><p className="muted">{analysis.questionnaire.chief_concerns.join("、") || "未识别"}</p></div>
                  <div><strong>健康目标</strong><p className="muted">{analysis.questionnaire.goals.join("、") || "未识别"}</p></div>
                  <div><strong>主要系统负担</strong><p className="muted">{Object.entries(analysis.questionnaire.msq_system_scores).sort(([, left], [, right]) => right - left).slice(0, 5).map(([key, value]) => `${key} ${value}`).join("；") || "未识别"}</p></div>
                  <div><strong>有效症状</strong><p className="muted">{analysis.questionnaire.symptoms.length ? `共 ${analysis.questionnaire.symptoms.length} 项：${analysis.questionnaire.symptoms.slice(0, 8).join("、")}${analysis.questionnaire.symptoms.length > 8 ? "等" : ""}` : "未识别"}</p></div>
                </div>
                <p className="muted">MSQ 由固定模板结构化提取；扫描版由模型单次视觉识别兜底。仅供查看，不进入异常指标校对区。</p>
              </SectionCard>
            ) : null}

            <SectionCard title="异常发现校对" subtitle="Numeric and non-numeric findings">
              <label className="field"><span>校对医生</span><input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} /></label>
              <div className="abnormal-finding-list">
                {findings.map((finding, index) => (
                  <div className="abnormal-finding-card" key={finding.id}>
                    <div className="abnormal-finding-card__summary">
                      <div>
                        <strong className="abnormal-finding-card__name">{finding.name.trim() || "未命名异常"}</strong>
                        <p className="abnormal-finding-card__result">{findingResultLabel(finding)}</p>
                      </div>
                      <span className="indicator-status indicator-status--attention">{abnormalFlagLabel(finding.abnormal_flag)}</span>
                    </div>
                    <details className="abnormal-finding-card__editor">
                      <summary>展开校对</summary>
                      <div className="abnormal-finding-card__editor-body">
                        <div className="grid-two">
                          <label className="field"><span>异常名称</span><input value={finding.name} onChange={(event) => updateFinding(index, { name: event.target.value })} /></label>
                          <label className="field"><span>结果 / 非数值结论</span><input value={finding.result_text ?? ""} onChange={(event) => updateFinding(index, { result_text: event.target.value || null })} /></label>
                          <label className="field"><span>数值</span><input value={finding.raw_value ?? ""} onChange={(event) => updateFinding(index, { raw_value: event.target.value || null })} /></label>
                          <label className="field"><span>单位</span><input value={finding.unit ?? ""} onChange={(event) => updateFinding(index, { unit: event.target.value || null })} /></label>
                          <label className="field"><span>参考范围</span><input value={finding.reference_range ?? ""} onChange={(event) => updateFinding(index, { reference_range: event.target.value || null })} /></label>
                          <label className="field"><span>异常方向</span><select value={finding.abnormal_flag} onChange={(event) => updateFinding(index, { abnormal_flag: event.target.value })}><option value="high">偏高</option><option value="low">偏低</option><option value="positive">阳性/存在</option><option value="unknown">未指定</option></select></label>
                        </div>
                        <details className="abnormal-finding-card__evidence">
                          <summary>查看来源证据</summary>
                          <div className="abnormal-finding-card__evidence-body">
                            <div className="grid-two">
                              <label className="field"><span>来源文件</span><select value={finding.source_file_id} onChange={(event) => { const file = validFiles.find((item) => item.id === event.target.value); updateFinding(index, { source_file_id: event.target.value, source_file_name: file?.filename ?? finding.source_file_name }); }}>{validFiles.map((file) => <option value={file.id} key={file.id}>{file.filename}</option>)}</select></label>
                              <label className="field"><span>页码</span><input type="number" min={1} value={finding.source_page} onChange={(event) => updateFinding(index, { source_page: Number(event.target.value) || 1 })} /></label>
                            </div>
                            <label className="field"><span>原文证据</span><textarea rows={3} value={finding.source_text} onChange={(event) => updateFinding(index, { source_text: event.target.value })} /></label>
                            <p className="muted">{evidenceLabel(finding.evidence_status)}{finding.evidence_notes.length ? ` · ${finding.evidence_notes.join("；")}` : ""}</p>
                          </div>
                        </details>
                        <button type="button" className="secondary-button secondary-button--danger" disabled={busy} onClick={() => setFindings((current) => current.filter((_, itemIndex) => itemIndex !== index))}>删除此项</button>
                      </div>
                    </details>
                  </div>
                ))}
                {!findings.length ? <p className="muted">模型未给出异常；医生仍可人工补充。</p> : null}
              </div>
              <div className="button-row">
                <button type="button" className="secondary-button" disabled={busy || !validFiles.length} onClick={addFinding}>补充异常</button>
                <button type="button" className="primary-button" disabled={busy || !canReview} onClick={() => void handleReviewAndGenerate()}>
                  {analysis.status === "reviewed" ? "重试生成营养素草案" : "保存校对并生成营养素草案"}
                </button>
              </div>
              <div aria-live="polite">
                {reviewActionError ? <p className="error-text">{reviewActionError}</p> : null}
                {reviewActionNotice ? <p className="muted">{reviewActionNotice}</p> : null}
              </div>
            </SectionCard>
          </>
        ) : null}

        {latestDraft ? (
          <SectionCard title="营养素草案审核与发布" subtitle="Existing review and publish flow">
            <p className="muted">草案 {latestDraft.id} · 状态 {latestDraft.status} · 置信度 {Math.round(latestDraft.confidence * 100)}%</p>
            <div className="stack">
              {latestDraft.recommended_skus.map((item) => (
                <label className="file-row" key={item.sku_id}>
                  <div><strong>{item.display_name}</strong><p className="muted">{item.dosage} · {item.reason}</p></div>
                  <span><input type="checkbox" checked={!excludedSkuIds.includes(item.sku_id)} onChange={(event) => setExcludedSkuIds((current) => event.target.checked ? current.filter((id) => id !== item.sku_id) : [...current, item.sku_id])} /> 纳入</span>
                </label>
              ))}
              {!latestDraft.recommended_skus.length ? (
                <p className="error-text">当前草案没有营养素推荐，不能审核发布，请重新生成草案。</p>
              ) : null}
            </div>
            <div className="field publishable-editor-field">
              <div className="publishable-editor-head">
                <span>最终发布内容</span>
                <div className="inline-actions publishable-editor-actions">
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={busy}
                    onClick={() => setPublishableEditorExpanded(true)}
                  >
                    放大编辑
                  </button>
                </div>
              </div>
              <textarea
                className="publishable-editor-textarea"
                rows={18}
                value={publishableSummary}
                onChange={(event) => setPublishableSummary(event.target.value)}
                aria-label="最终发布内容"
              />
            </div>
            <div className="button-row">
              <button type="button" className="primary-button" disabled={busy || Boolean(payload.review_decision) || includedRecommendationCount === 0} onClick={() => void handleApprove()}>{payload.review_decision ? "已审核发布" : "审核并发布"}</button>
              {payload.review_decision ? <a className="secondary-button" href={getPdfReportUrl(latestDraft.id)} target="_blank" rel="noreferrer">下载 PDF</a> : null}
            </div>
          </SectionCard>
        ) : null}

        <SectionCard title="审计记录" subtitle="Audit trail">
          <div className="stack">{payload.audit_logs.slice(0, 20).map((log) => <div className="file-row" key={log.id}><div><strong>{log.action}</strong><p className="muted">{log.actor_id} · {new Date(log.created_at).toLocaleString("zh-CN")}</p></div></div>)}</div>
        </SectionCard>
      </div>

      {publishableEditorExpanded ? (
        <div className="report-editor-overlay" role="presentation">
          <div
            className="report-editor-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="report-editor-title"
          >
            <div className="report-editor-dialog__head">
              <div>
                <p className="section-card__eyebrow">Publishable report</p>
                <h3 id="report-editor-title">最终发布内容</h3>
              </div>
              <div className="inline-actions">
                <button
                  type="button"
                  className="primary-button"
                  onClick={() => setPublishableEditorExpanded(false)}
                >
                  完成
                </button>
              </div>
            </div>
            <textarea
              className="report-editor-dialog__textarea"
              value={publishableSummary}
              onChange={(event) => setPublishableSummary(event.target.value)}
              aria-label="放大编辑最终发布内容"
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}
