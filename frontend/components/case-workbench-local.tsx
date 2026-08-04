"use client";

import Link from "next/link";
import { ChangeEvent, useEffect, useMemo, useState } from "react";

import {
  approveDraft,
  downloadPdfReport,
  deleteCaseFile,
  fetchCase,
  fetchCurrentUser,
  fetchLatestCaseAnalysis,
  retryDraftGeneration,
  reviewAndGenerate,
  startCaseAnalysis,
  updateClinicalSummary,
  uploadCaseFile
} from "../lib/api";
import {
  AbnormalFinding,
  CaseAnalysis,
  CaseDetailResponse,
  DraftRecommendationItem,
  RecommendationDraft
} from "../lib/types";
import { SectionCard } from "./section-card";
import { StatusPillLocal } from "./status-pill-local";
import { OperationProgress, OperationProgressState } from "./operation-progress";
import { MarkdownEditor, MarkdownViewMode } from "./markdown-editor";

const ACTIVE_ANALYSIS = new Set(["queued", "preparing", "analyzing_documents", "synthesizing", "validating"]);
const ACTIVE_FINAL_GENERATION = new Set([
  "queued",
  "final_synthesizing",
  "validating_support_needs",
  "mapping_products",
  "checking_safety",
  "generating_draft"
]);

const FINAL_GENERATION_LABELS: Record<string, string> = {
  idle: "等待医生校对",
  queued: "医生校对已保存，任务排队中",
  final_synthesizing: "最终病例深度综合",
  validating_support_needs: "支持需求校验",
  mapping_products: "产品能力匹配",
  checking_safety: "禁忌与安全检查",
  generating_draft: "生成营养素草案",
  ready: "草案生成完成",
  failed: "草案生成失败"
};

const BODY_SYSTEM_LABELS: Record<string, string> = {
  digestive_gut: "消化系统/肠道",
  liver_detox: "肝脏/解毒系统",
  immune_inflammation: "免疫/炎症系统",
  endocrine_metabolic: "内分泌/代谢系统",
  cardiovascular: "心血管系统",
  respiratory: "呼吸系统",
  neuro_sleep: "神经/认知/睡眠系统",
  bone_muscle: "骨骼/肌肉系统",
  urinary_renal: "泌尿/肾脏系统",
  reproductive_breast: "生殖/妇科/乳腺系统",
  skin_mucosa: "皮肤/黏膜系统"
};

const UNCOVERED_SYSTEM_REASON_LABELS: Record<string, string> = {
  no_approved_mapping: "暂无批准的产品映射",
  evidence_not_eligible: "当前证据未达到推荐条件",
  safety_excluded: "候选产品未通过安全校验"
};

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

const REPORT_LIST_SECTIONS = new Set([
  "异常指标汇总",
  "慢性食物敏感检测结果",
  "生活方式干预",
  "生活方式干预处方",
  "首月营养素干预方案",
  "现有补充剂调整建议",
  "需要补充确认",
  "待确认项",
  "复查与随访计划",
  "安全警示"
]);

const SOURCE_REPORT_ORDER = [
  "核心结论与健康画像",
  "异常指标汇总",
  "慢性食物敏感检测结果",
  "功能医学系统失衡分析",
  "生活方式干预",
  "首月营养素干预方案",
  "总医嘱说明",
  "方案总结",
  "复查与随访计划",
  "安全警示"
];

const STANDARD_REPORT_ORDER = [
  "核心结论与健康画像",
  "异常指标汇总",
  "功能医学系统失衡分析",
  "生活方式干预处方",
  "首月营养素干预方案",
  "总医嘱说明",
  "现有补充剂调整建议",
  "待确认项",
  "方案总结",
  "复查与随访计划",
  "安全警示"
];

function chineseChapterNumber(value: number) {
  const digits = "零一二三四五六七八九";
  if (value < 10) return digits[value];
  if (value < 20) return `十${value % 10 ? digits[value % 10] : ""}`;
  if (value < 100) {
    const tens = Math.floor(value / 10);
    const ones = value % 10;
    return `${digits[tens]}十${ones ? digits[ones] : ""}`;
  }
  return String(value);
}

function reportText(draft: RecommendationDraft | null | undefined) {
  if (!draft) return "";
  const order = draft.source_analysis_id ? SOURCE_REPORT_ORDER : STANDARD_REPORT_ORDER;
  let chapterIndex = 0;
  const blocks = order.flatMap((title) => {
    const raw = draft.report_sections[title];
    const items = (Array.isArray(raw) ? raw : [raw]).filter(Boolean);
    if (!items.length) return [];
    const heading = title === "总医嘱说明"
      ? "### 总医嘱说明"
      : `## ${chineseChapterNumber(++chapterIndex)}、${title}`;
    const renderedItems = items.map((item) => {
      const text = String(item).trim();
      if (text.startsWith("### ")) return text;
      return REPORT_LIST_SECTIONS.has(title) ? `- ${text}` : text;
    });
    return [[heading, ...renderedItems].join("\n")];
  });
  return ["# 功能医学综合分析与首月干预方案", ...blocks].join("\n\n");
}

type DosageOverrideDraft = {
  option_id: string;
  note: string;
};

type UploadFailure = {
  id: string;
  filename: string;
  message: string;
};

async function fileSha256(file: File): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  try {
    const digest = await globalThis.crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
  } catch {
    return null;
  }
}

function effectiveDosageOption(item: DraftRecommendationItem, overrides: Record<string, DosageOverrideDraft>) {
  const selectedId = overrides[item.sku_id]?.option_id || item.dosage_option_id;
  return item.dosage_options?.find((option) => option.option_id === selectedId);
}

function effectiveDosageText(item: DraftRecommendationItem, overrides: Record<string, DosageOverrideDraft>) {
  const option = effectiveDosageOption(item, overrides);
  if (!option) return item.dosage;
  const requiresReview = item.dosage.startsWith("医生复核剂量；") || option.requires_review;
  return `${requiresReview ? "医生复核剂量；" : ""}${option.display_text}`;
}

function cloneFindings(items: AbnormalFinding[]) {
  return items.map((item) => ({
    ...item,
    evidence_notes: [...item.evidence_notes],
    system_id_candidates: [...(item.system_id_candidates ?? [])],
    support_goal_candidates: [...(item.support_goal_candidates ?? [])],
    system_ids: [...(item.system_ids ?? [])],
    support_goals: [...(item.support_goals ?? [])],
    standardization_notes: [...(item.standardization_notes ?? [])]
  }));
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
  if (flag === "genetic_risk") return "遗传风险";
  if (flag === "patient_reported") return "患者自述";
  return "异常";
}

function isPatientReportedFinding(finding: AbnormalFinding) {
  return finding.abnormal_flag === "patient_reported";
}

function findingNeedsEvidenceReview(finding: AbnormalFinding) {
  return !isPatientReportedFinding(finding) && finding.evidence_status === "needs_review";
}

function findingHasVerifiedEvidence(finding: AbnormalFinding) {
  return isPatientReportedFinding(finding) || finding.evidence_status === "verified_text";
}

function findingEvidenceLabel(finding: AbnormalFinding) {
  if (isPatientReportedFinding(finding)) return "患者自述";
  return `${evidenceLabel(finding.evidence_status)}${finding.evidence_notes.length ? ` · ${finding.evidence_notes.join("；")}` : ""}`;
}

function findingResultLabel(finding: AbnormalFinding) {
  const resultText = finding.result_text?.trim() || "";
  const rawValue = finding.raw_value?.trim() || "";
  const hasNumericContext = Boolean(finding.unit?.trim() || finding.reference_range?.trim());
  const hasNumericValue = /[+-]?\d+(?:,\d{3})*(?:\.\d+)?/.test(rawValue || resultText);
  if (hasNumericContext && !hasNumericValue) return "具体数值待确认";
  return resultText
    || [rawValue, finding.unit].filter(Boolean).join(" ")
    || finding.interpretation?.trim()
    || "已发现异常";
}

function standardizationStatusLabel(status: AbnormalFinding["standardization_status"]) {
  if (status === "validated") return "已完成精准结构化匹配。";
  if (status === "support_mapped") return "已识别相关营养支持方向，生成草案时仍由本地产品与安全规则校验。";
  if (status === "system_mapped") return "已归入相关身体系统，不会单独触发营养素推荐。";
  if (status === "unmapped") return "暂未建立可靠映射，该异常仍会保留在报告中。";
  if (status === "rejected") return "模型候选未通过本地校验，该异常仍会保留在报告中。";
  return "";
}

function analysisProgressPercent(analysis: CaseAnalysis) {
  if (analysis.status === "queued") return 5;
  if (analysis.status === "preparing") return 12;
  if (analysis.status === "analyzing_documents") {
    return 18 + Math.round((analysis.progress_current / Math.max(analysis.progress_total, 1)) * 58);
  }
  if (analysis.status === "synthesizing") return 82;
  if (analysis.status === "validating") return 94;
  return 100;
}

function analysisStageDetail(analysis: CaseAnalysis) {
  const total = Math.max(analysis.progress_total, 0);
  const completed = Math.min(analysis.progress_current, total);
  if (analysis.status === "analyzing_documents") {
    const fileSummary = `文件分析 ${completed}/${total} ${completed >= total && total > 0 ? "已完成" : "处理中"}`;
    return analysis.current_file_name ? `${fileSummary}：${analysis.current_file_name}` : fileSummary;
  }
  if (analysis.status === "synthesizing") return `文件分析 ${total}/${total} 已完成，正在进行病例级综合`;
  if (analysis.status === "validating") return "病例级综合已完成，正在进行证据校验";
  return ANALYSIS_LABELS[analysis.status] ?? analysis.status;
}

export function CaseWorkbenchLocal({ caseId }: { caseId: string }) {
  const [payload, setPayload] = useState<CaseDetailResponse | null>(null);
  const [analysis, setAnalysis] = useState<CaseAnalysis | null>(null);
  const [findings, setFindings] = useState<AbnormalFinding[]>([]);
  const [reviewerId, setReviewerId] = useState("reviewer-01");
  const [clinicalSummary, setClinicalSummary] = useState("");
  const [publishableSummary, setPublishableSummary] = useState("");
  const [publishableEditorExpanded, setPublishableEditorExpanded] = useState(false);
  const [publishableEditorMode, setPublishableEditorMode] = useState<MarkdownViewMode>("split");
  const [excludedSkuIds, setExcludedSkuIds] = useState<string[]>([]);
  const [dosageOverrides, setDosageOverrides] = useState<Record<string, DosageOverrideDraft>>({});
  const [thirdPartyConfirmed, setThirdPartyConfirmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reviewActionError, setReviewActionError] = useState<string | null>(null);
  const [reviewActionNotice, setReviewActionNotice] = useState<string | null>(null);
  const [operation, setOperation] = useState<OperationProgressState | null>(null);
  const [uploadFailures, setUploadFailures] = useState<UploadFailure[]>([]);
  const [findingFilter, setFindingFilter] = useState<"all" | "attention" | "needs_review" | "verified">("all");
  const [findingSearch, setFindingSearch] = useState("");

  const visibleFindings = useMemo(() => findings
    .map((finding, index) => ({ finding, index }))
    .filter(({ finding }) => {
      const query = findingSearch.trim().toLowerCase();
      const matchesSearch = !query || [finding.name, finding.result_text, finding.interpretation, finding.source_text]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(query));
      const matchesFilter = findingFilter === "all"
        || (findingFilter === "attention" && ["high", "low", "positive", "genetic_risk"].includes(finding.abnormal_flag))
        || (findingFilter === "needs_review" && findingNeedsEvidenceReview(finding))
        || (findingFilter === "verified" && findingHasVerifiedEvidence(finding));
      return matchesSearch && matchesFilter;
    }), [findings, findingFilter, findingSearch]);

  async function loadCase() {
    const next = await fetchCase(caseId);
    setPayload(next);
    setClinicalSummary(next.case.clinical_summary_text ?? "");
    setPublishableSummary(reportText(next.latest_draft));
    const systemSelections = Object.fromEntries(
      (next.latest_draft?.recommended_skus ?? [])
        .filter((item) => item.dosage_option_id)
        .map((item) => [item.sku_id, { option_id: item.dosage_option_id as string, note: "" }])
    );
    const reviewedSelections = (
      next.review_decision?.edits?.dosage_overrides
      && typeof next.review_decision.edits.dosage_overrides === "object"
    )
      ? next.review_decision.edits.dosage_overrides as Record<string, DosageOverrideDraft>
      : {};
    setDosageOverrides({ ...systemSelections, ...reviewedSelections });
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
    const shouldPoll = analysis && (
      ACTIVE_ANALYSIS.has(analysis.status)
      || ACTIVE_FINAL_GENERATION.has(analysis.final_generation_status)
    );
    if (!shouldPoll) return;
    let stopped = false;
    let inFlight = false;
    async function poll() {
      if (stopped || inFlight) return;
      inFlight = true;
      await loadLatestAnalysis()
        .then((next) => {
          if (next && !ACTIVE_ANALYSIS.has(next.status) && !ACTIVE_FINAL_GENERATION.has(next.final_generation_status)) {
            void loadCase();
          }
        })
        .catch((err) => setError(err instanceof Error ? err.message : "读取分析进度失败"))
        .finally(() => { inFlight = false; });
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 1800);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [analysis?.id, analysis?.status, analysis?.final_generation_status]);

  useEffect(() => {
    if (!analysis || analysis.status !== "reviewed") return;
    const finalStatus = analysis.final_generation_status;
    if (ACTIVE_FINAL_GENERATION.has(finalStatus)) {
      setOperation({
        placement: "draft",
        title: "生成结构化草案",
        stage: FINAL_GENERATION_LABELS[finalStatus] ?? finalStatus,
        percent: analysis.final_generation_progress,
        status: "running"
      });
      setReviewActionNotice(FINAL_GENERATION_LABELS[finalStatus] ?? finalStatus);
      setReviewActionError(null);
      return;
    }
    if (finalStatus === "ready") {
      setOperation({
        placement: "draft",
        title: "生成结构化草案",
        stage: "结构化草案已生成，等待医生审核。",
        percent: 100,
        status: "success"
      });
      setReviewActionNotice("异常校对已保存，营养素草案已生成并进入待审核状态。");
      setReviewActionError(null);
      return;
    }
    if (finalStatus === "failed") {
      const message = `校对已保存，草案生成失败：${analysis.final_generation_error ?? "未知错误"}。可直接重试，不会重新读取 PDF。`;
      setOperation({ placement: "draft", title: "生成结构化草案", stage: message, percent: 100, status: "error" });
      setReviewActionError(message);
      setReviewActionNotice(null);
    }
  }, [analysis?.final_generation_status, analysis?.final_generation_progress, analysis?.final_generation_error]);

  useEffect(() => {
    if (!analysis) return;
    setOperation((current) => {
      const isActive = ACTIVE_ANALYSIS.has(analysis.status);
      if (current?.placement !== "analysis" && !isActive) return current;
      if (isActive) {
        return {
          placement: "analysis",
          title: "综合病例分析",
          stage: analysisStageDetail(analysis),
          percent: analysisProgressPercent(analysis),
          status: "running"
        };
      }
      if (analysis.status === "ready_for_review" || analysis.status === "reviewed") {
        return {
          placement: "analysis",
          title: "综合病例分析",
          stage: "综合分析已完成，可以开始医生校对。",
          percent: 100,
          status: "success"
        };
      }
      if (analysis.status === "failed" || analysis.status === "stale") {
        return {
          placement: "analysis",
          title: "综合病例分析",
          stage: analysis.error_message || ANALYSIS_LABELS[analysis.status],
          percent: 100,
          status: "error"
        };
      }
      return current;
    });
  }, [analysis?.status, analysis?.progress_current, analysis?.progress_total, analysis?.current_file_name, analysis?.error_message]);

  const validFiles = useMemo(
    () => payload?.case.files.filter((file) => file.intake_status !== "invalid") ?? [],
    [payload]
  );

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const files = Array.from(input.files ?? []);
    input.value = "";
    if (!files.length) return;
    const knownHashes = new Set(
      (payload?.case.files ?? [])
        .map((file) => file.content_sha256)
        .filter((digest): digest is string => Boolean(digest))
    );
    const failures: UploadFailure[] = [];
    let uploadedCount = 0;
    let skippedCount = 0;
    try {
      setBusy(true);
      setUploadFailures([]);
      setOperation({ placement: "upload", title: "上传并预解析资料", stage: `准备处理 ${files.length} 份文件`, percent: 0, status: "running" });
      for (const [index, file] of files.entries()) {
        setOperation({
          placement: "upload",
          title: "上传并预解析资料",
          stage: `正在检查第 ${index + 1}/${files.length} 份：${file.name}`,
          percent: (index / files.length) * 100,
          status: "running"
        });
        const digest = await fileSha256(file);
        if (digest && knownHashes.has(digest)) {
          skippedCount += 1;
          setOperation({
            placement: "upload",
            title: "上传并预解析资料",
            stage: `已跳过重复文件：${file.name}`,
            percent: ((index + 1) / files.length) * 100,
            status: "running"
          });
          continue;
        }
        try {
          const response = await uploadCaseFile(caseId, file, (filePercent) => {
            const percent = ((index + filePercent / 100) / files.length) * 100;
            setOperation({
              placement: "upload",
              title: "上传并预解析资料",
              stage: `正在处理第 ${index + 1}/${files.length} 份：${file.name}`,
              percent,
              status: "running"
            });
          });
          const latestOperation: CaseDetailResponse["operation"] = response.operation ?? null;
          if (latestOperation && !latestOperation.success) {
            throw new Error(latestOperation.message);
          }
          uploadedCount += 1;
          if (digest) knownHashes.add(digest);
        } catch (err) {
          failures.push({
            id: `${file.name}-${file.size}-${file.lastModified}-${index}`,
            filename: file.name,
            message: err instanceof Error ? err.message : "上传失败"
          });
          setOperation({
            placement: "upload",
            title: "上传并预解析资料",
            stage: `${file.name} 上传失败，继续处理其余文件。`,
            percent: ((index + 1) / files.length) * 100,
            status: "running"
          });
        }
      }
      setUploadFailures(failures);
      await loadCase();
      if (uploadedCount > 0) await loadLatestAnalysis({ quiet404: true });
      const completedCount = uploadedCount + skippedCount;
      const summary = `成功上传 ${uploadedCount} 份${skippedCount ? `，跳过重复文件 ${skippedCount} 份` : ""}${failures.length ? `，失败 ${failures.length} 份` : ""}。`;
      setOperation({
        placement: "upload",
        title: "上传并预解析资料",
        stage: failures.length ? summary : `${summary}可继续开始综合分析。`,
        detail: failures.length ? "失败文件及具体原因已在下方列出，其他文件不受影响。" : null,
        percent: 100,
        status: failures.length ? (completedCount > 0 ? "partial" : "error") : "success"
      });
      setNotice(completedCount > 0 ? "已完成本批文件处理。上传阶段未调用大模型，也未生成任何指标或草案。" : null);
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "上传失败";
      setError(message);
      setOperation({ placement: "upload", title: "上传并预解析资料", stage: message, percent: 100, status: "error" });
    } finally {
      setBusy(false);
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
      setOperation({ placement: "analysis", title: "综合病例分析", stage: "正在创建分析任务……", percent: 5, status: "running" });
      const next = await startCaseAnalysis(caseId, thirdPartyConfirmed);
      setAnalysis(next);
      setFindings([]);
      setNotice("综合分析任务已创建，可留在页面查看逐文件进度。");
      setOperation({ placement: "analysis", title: "综合病例分析", stage: "任务已创建，正在读取后端状态……", percent: 8, status: "running" });
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "无法开始综合分析";
      setError(message);
      setOperation({ placement: "analysis", title: "综合病例分析", stage: message, percent: 100, status: "error" });
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
      setOperation({ placement: "draft", title: "生成结构化草案", stage: "保存医生校对", percent: 3, status: "running" });
      setReviewActionError(null);
      setReviewActionNotice("正在保存医生校对……");
      const result = await reviewAndGenerate(
        caseId,
        analysis.id,
        reviewerId,
        analysis.revision,
        findings
      );
      setAnalysis(result.analysis);
      const message = "异常校对已保存，草案生成任务已进入后台队列。可以刷新页面或离开后再回来查看进度。";
      setNotice(message);
      setReviewActionNotice(message);
      setOperation({ placement: "draft", title: "生成结构化草案", stage: "任务已排队", percent: result.analysis.final_generation_progress, status: "running" });
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "保存校对并生成草案失败";
      setError(message);
      setOperation({ placement: "draft", title: "生成结构化草案", stage: message, percent: 100, status: "error" });
      setReviewActionError(message);
      setReviewActionNotice(null);
    } finally {
      setBusy(false);
    }
  }

  async function handleRetryDraftGeneration() {
    if (!analysis) return;
    try {
      setBusy(true);
      setReviewActionError(null);
      const next = await retryDraftGeneration(caseId, analysis.id);
      setAnalysis(next);
      setReviewActionNotice("草案生成任务已重新排队；已完成的深度综合会直接复用。");
      setOperation({ placement: "draft", title: "生成结构化草案", stage: "任务已重新排队", percent: next.final_generation_progress, status: "running" });
    } catch (err) {
      const message = err instanceof Error ? err.message : "重试草案生成失败";
      setReviewActionError(message);
      setOperation({ placement: "draft", title: "生成结构化草案", stage: message, percent: 100, status: "error" });
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
    const changedDosageOverrides = Object.fromEntries(
      includedRecommendations
        .map((item) => [item, dosageOverrides[item.sku_id]] as const)
        .filter(([item, override]) => override && override.option_id !== item.dosage_option_id)
        .map(([item, override]) => [item.sku_id, override])
    );
    const missingNote = includedRecommendations.find((item) => {
      const override = dosageOverrides[item.sku_id];
      return override && override.option_id !== item.dosage_option_id && !override.note.trim();
    });
    if (missingNote) {
      setError(`${missingNote.display_name} 改为非系统默认档位时必须填写调整备注。`);
      return;
    }
    try {
      setBusy(true);
      setOperation({ placement: "report", title: "审核并生成报告", stage: "正在保存审核结果并生成报告文件……", percent: 55, status: "running" });
      await approveDraft(draft.id, reviewerId, publishableSummary, {
        excluded_sku_ids: excludedSkuIds,
        dosage_overrides: changedDosageOverrides
      });
      await loadCase();
      setNotice("报告已审核发布，正在自动导出 PDF。");
      const downloaded = await handleDownloadReport(draft.id);
      if (!downloaded) throw new Error("PDF 自动下载失败，请稍后重试");
      setNotice("报告已审核发布，PDF 已自动下载。");
      setOperation({ placement: "report", title: "审核并生成报告", stage: "报告已发布，PDF 已自动下载。", percent: 100, status: "success" });
      setError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "审核发布失败";
      setError(message);
      setOperation({ placement: "report", title: "审核并生成报告", stage: message, percent: 100, status: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function handleDownloadReport(draftId: string): Promise<boolean> {
    try {
      setBusy(true);
      setOperation({ placement: "report", title: "导出 PDF 报告", stage: "正在生成并下载报告文件……", percent: 12, status: "running" });
      await downloadPdfReport(draftId, (percent) => {
        setOperation({ placement: "report", title: "导出 PDF 报告", stage: "正在下载报告文件……", percent: Math.max(12, percent), status: "running" });
      });
      setOperation({ placement: "report", title: "导出 PDF 报告", stage: "PDF 已下载。", percent: 100, status: "success" });
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : "报告下载失败";
      setError(message);
      setOperation({ placement: "report", title: "导出 PDF 报告", stage: message, percent: 100, status: "error" });
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="muted">正在加载病例工作台...</p>;
  if (!payload) return <p className="error-text">{error ?? "病例工作台加载失败"}</p>;

  const caseRecord = payload.case;
  const latestDraft = payload.latest_draft;
  const canStart = validFiles.length > 0 && (!analysis || ["failed", "stale"].includes(analysis.status));
  const canReview = analysis
    && ["ready_for_review", "reviewed"].includes(analysis.status)
    && !ACTIVE_FINAL_GENERATION.has(analysis.final_generation_status);
  const includedRecommendationCount = latestDraft
    ? latestDraft.recommended_skus.filter((item) => !excludedSkuIds.includes(item.sku_id)).length
    : 0;
  const ruleExcludedDecisions = (latestDraft?.safety_decisions ?? []).filter(
    (decision) => decision.action === "exclude"
  );
  const systemCoverageRows = (latestDraft?.structured_system_findings ?? []).map((finding) => {
    const coveringItems = (latestDraft?.recommended_skus ?? []).filter((item) =>
      (item.covered_system_ids?.length ? item.covered_system_ids : item.primary_system_id ? [item.primary_system_id] : [])
        .includes(finding.system_id)
    );
    const includedItems = coveringItems.filter((item) => !excludedSkuIds.includes(item.sku_id));
    return {
      systemId: finding.system_id,
      systemName: BODY_SYSTEM_LABELS[finding.system_id] ?? finding.system_name,
      includedNames: includedItems.map((item) => item.display_name),
      excludedNames: coveringItems.map((item) => item.display_name),
      uncoveredReason: latestDraft?.uncovered_system_reasons?.[finding.system_id]
    };
  });
  const workflowStep = payload.review_decision ? 5 : latestDraft ? 4 : analysis && ["ready_for_review", "reviewed"].includes(analysis.status) ? 3 : analysis ? 2 : caseRecord.files.length ? 1 : 0;

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

      <nav className="workflow-steps" aria-label="病例处理流程">
        {["资料上传", "综合分析", "医生校对", "草案编辑", "审核发布"].map((label, index) => (
          <div className={`workflow-step${workflowStep >= index + 1 ? " workflow-step--done" : ""}${workflowStep === index ? " workflow-step--active" : ""}`} key={label}>
            <span>{workflowStep > index ? "✓" : index + 1}</span><strong>{label}</strong>
          </div>
        ))}
      </nav>

      <div className="workbench-grid">
        <SectionCard title="资料上传与分析准备" subtitle="01 · Intake" tone="intake">
          <label className="upload-dropzone">
            <input
              type="file"
              multiple
              accept=".pdf,.docx,.pptx,.txt,.md,.csv,.json,.png,.jpg,.jpeg,.bmp,.gif,.tif,.tiff,.webp"
              onChange={handleUpload}
              disabled={busy}
            />
            <span>上传病例报告、MSQ、肠道报告、慢性食物敏感报告或总结截图</span>
            <small>仅做轻量预检；明显无关文件会提示但不会阻止上传。默认单文件 50 MB、单个 PDF 最多 50 页。</small>
          </label>
          {operation?.placement === "upload" ? <OperationProgress operation={operation} /> : null}
          <div className="stack">
            {uploadFailures.map((failure) => (
              <div className="file-row file-row--error" key={failure.id}>
                <div>
                  <strong>{failure.filename}</strong>
                  <p className="error-text">上传失败：{failure.message}</p>
                </div>
                <span className="indicator-status indicator-status--attention">上传失败</span>
              </div>
            ))}
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
            {!caseRecord.files.length && !uploadFailures.length ? <p className="muted">尚未上传资料。</p> : null}
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
            <span><input type="checkbox" checked={thirdPartyConfirmed} onChange={(event) => setThirdPartyConfirmed(event.target.checked)} /> 已确认获得将本病例资料发送至配置的第三方大模型处理的授权</span>
          </label>
          <button type="button" className="primary-button" disabled={busy || !canStart || !thirdPartyConfirmed} onClick={() => void handleStartAnalysis()}>
            {analysis?.status === "failed" || analysis?.status === "stale" ? "重新开始综合分析" : "确认资料并开始综合分析"}
          </button>
          {operation?.placement === "analysis" ? <OperationProgress operation={operation} /> : null}
          {!analysis ? <p className="muted">确认资料后，这里会显示逐文件处理进度。</p> : (
            <div className="analysis-status-panel">
              <div className="file-row">
                <div>
                  <strong>{ANALYSIS_LABELS[analysis.status] ?? analysis.status}</strong>
                  <p className="muted">分析版本 {analysis.version} · 模型 {analysis.model_version}</p>
                </div>
                <span className="indicator-status indicator-status--info">{analysis.progress_current}/{analysis.progress_total}</span>
              </div>
              <div className="analysis-progress-meta"><span>{analysisStageDetail(analysis)}</span><strong>{analysisProgressPercent(analysis)}%</strong></div>
              <progress className="analysis-progress" max={100} value={analysisProgressPercent(analysis)} aria-label="病例综合分析总体进度" />
              {analysis.current_file_name ? <p className="muted">正在处理：{analysis.current_file_name}</p> : null}
              {analysis.error_message ? <p className="error-text">{analysis.error_code}: {analysis.error_message}</p> : null}
              {analysis.warnings.map((warning, index) => <p className="muted" key={`${warning}-${index}`}>⚠ {warning}</p>)}
              {analysis.ignored_files.length ? <p className="muted">模型判断已忽略：{analysis.ignored_files.join("、")}</p> : null}
            </div>
          )}
        </SectionCard>

        {analysis && ["ready_for_review", "reviewed"].includes(analysis.status) ? (
          <>
            <SectionCard title="初步病例综合" subtitle="02 · Model synthesis" tone="analysis">
              <div className="synthesis-sections">
                <section className="synthesis-block"><h3>病例总结</h3><p className="case-synthesis-text">{analysis.reviewed_case_summary ?? analysis.case_summary ?? "暂无"}</p></section>
                <section className="synthesis-block"><h3>功能医学系统发现</h3>
                  <ul>{(analysis.reviewed_system_findings.length ? analysis.reviewed_system_findings : analysis.system_findings).map((item) => <li key={item}>{item}</li>)}</ul>
                </section>
                {analysis.questionnaire ? (
                  <section className="synthesis-block synthesis-block--msq">
                    <div className="synthesis-block__head"><h3>MSQ 摘要</h3><span>只读结构化结果</span></div>
                    <div className="grid-two msq-summary-grid">
                      <div><strong>主要诉求</strong><p className="muted">{analysis.questionnaire.chief_concerns.join("、") || "未识别"}</p></div>
                      <div><strong>健康目标</strong><p className="muted">{analysis.questionnaire.goals.join("、") || "未识别"}</p></div>
                      <div><strong>主要系统负担</strong><p className="muted">{Object.entries(analysis.questionnaire.msq_system_scores).sort(([, left], [, right]) => right - left).slice(0, 5).map(([key, value]) => `${key} ${value}`).join("；") || "未识别"}</p></div>
                      <div><strong>有效症状</strong><p className="muted">{analysis.questionnaire.symptoms.length ? `共 ${analysis.questionnaire.symptoms.length} 项：${analysis.questionnaire.symptoms.slice(0, 8).join("、")}${analysis.questionnaire.symptoms.length > 8 ? "等" : ""}` : "未识别"}</p></div>
                    </div>
                    <p className="muted">MSQ 由固定模板结构化提取；扫描版由模型单次视觉识别兜底，不进入异常指标校对区。</p>
                  </section>
                ) : null}
              </div>
            </SectionCard>

            <SectionCard title="异常发现校对" subtitle="03 · Clinical review" tone="review">
              <label className="field"><span>校对医生</span><input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} /></label>
              <div className="finding-toolbar">
                <div className="finding-filter" role="group" aria-label="异常指标筛选">
                  {([
                    ["all", `全部 ${findings.length}`],
                    ["attention", `异常 ${findings.filter((item) => ["high", "low", "positive", "genetic_risk"].includes(item.abnormal_flag)).length}`],
                    ["needs_review", `待确认 ${findings.filter(findingNeedsEvidenceReview).length}`],
                    ["verified", `已核对 ${findings.filter(findingHasVerifiedEvidence).length}`]
                  ] as const).map(([value, label]) => (
                    <button type="button" key={value} className={`finding-filter__button${findingFilter === value ? " is-active" : ""}`} onClick={() => setFindingFilter(value)}>{label}</button>
                  ))}
                </div>
                <input className="finding-search" value={findingSearch} onChange={(event) => setFindingSearch(event.target.value)} placeholder="搜索指标、结果或证据" aria-label="搜索异常指标" />
              </div>
              <div className="abnormal-finding-list">
                {visibleFindings.map(({ finding, index }) => (
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
                        {standardizationStatusLabel(finding.standardization_status) ? (
                          <p className="muted">{standardizationStatusLabel(finding.standardization_status)}</p>
                        ) : null}
                        <div className="grid-two">
                          <label className="field"><span>异常名称</span><input value={finding.name} onChange={(event) => updateFinding(index, { name: event.target.value })} /></label>
                          <label className="field"><span>结果 / 非数值结论</span><input value={finding.result_text ?? ""} onChange={(event) => updateFinding(index, { result_text: event.target.value || null })} /></label>
                          <label className="field"><span>数值</span><input value={finding.raw_value ?? ""} onChange={(event) => updateFinding(index, { raw_value: event.target.value || null })} /></label>
                          <label className="field"><span>单位</span><input value={finding.unit ?? ""} onChange={(event) => updateFinding(index, { unit: event.target.value || null })} /></label>
                          <label className="field"><span>参考范围</span><input value={finding.reference_range ?? ""} onChange={(event) => updateFinding(index, { reference_range: event.target.value || null })} /></label>
                          <label className="field"><span>异常方向</span><select value={finding.abnormal_flag} onChange={(event) => updateFinding(index, { abnormal_flag: event.target.value })}><option value="high">偏高</option><option value="low">偏低</option><option value="positive">阳性/存在</option><option value="genetic_risk">遗传风险</option><option value="patient_reported">患者自述</option><option value="unknown">未指定</option></select></label>
                        </div>
                        <details className="abnormal-finding-card__evidence">
                          <summary>查看来源证据</summary>
                          <div className="abnormal-finding-card__evidence-body">
                            <div className="grid-two">
                              <label className="field"><span>来源文件</span><select value={finding.source_file_id} onChange={(event) => { const file = validFiles.find((item) => item.id === event.target.value); updateFinding(index, { source_file_id: event.target.value, source_file_name: file?.filename ?? finding.source_file_name }); }}>{validFiles.map((file) => <option value={file.id} key={file.id}>{file.filename}</option>)}</select></label>
                              <label className="field"><span>页码</span><input type="number" min={1} value={finding.source_page} onChange={(event) => updateFinding(index, { source_page: Number(event.target.value) || 1 })} /></label>
                            </div>
                            <label className="field"><span>原文证据</span><textarea rows={3} value={finding.source_text} onChange={(event) => updateFinding(index, { source_text: event.target.value })} /></label>
                            {finding.report_explanation ? <p className="muted"><strong>报告解释：</strong>{finding.report_explanation}</p> : null}
                            {finding.neutral_interpretation ? <p className="muted"><strong>中性医学解释：</strong>{finding.neutral_interpretation}</p> : null}
                            <p className="muted">{findingEvidenceLabel(finding)}</p>
                          </div>
                        </details>
                        <button type="button" className="secondary-button secondary-button--danger" disabled={busy} onClick={() => setFindings((current) => current.filter((_, itemIndex) => itemIndex !== index))}>删除此项</button>
                      </div>
                    </details>
                  </div>
                ))}
                {!findings.length ? <p className="muted">模型未给出异常；医生仍可人工补充。</p> : null}
                {findings.length > 0 && !visibleFindings.length ? <p className="muted">当前筛选条件下没有匹配的异常指标。</p> : null}
              </div>
              <div className="button-row">
                <button type="button" className="secondary-button" disabled={busy || !validFiles.length} onClick={addFinding}>补充异常</button>
                <button
                  type="button"
                  className="primary-button"
                  disabled={busy || !canReview}
                  onClick={() => void (analysis.final_generation_status === "failed" ? handleRetryDraftGeneration() : handleReviewAndGenerate())}
                >
                  {analysis.final_generation_status === "failed"
                    ? "重试生成营养素草案"
                    : analysis.status === "reviewed"
                      ? "重新生成营养素草案"
                      : "保存校对并生成营养素草案"}
                </button>
              </div>
              {analysis.status === "reviewed" && analysis.final_generation_status !== "idle" ? (
                <div className="analysis-status-panel">
                  <div className="file-row">
                    <strong>{FINAL_GENERATION_LABELS[analysis.final_generation_status] ?? analysis.final_generation_status}</strong>
                    <span className="indicator-status indicator-status--info">{analysis.final_generation_progress}%</span>
                  </div>
                  <progress className="analysis-progress" max={100} value={analysis.final_generation_progress} aria-label="营养素草案生成进度" />
                  {analysis.final_generation_error ? <p className="error-text">{analysis.final_generation_error}</p> : null}
                </div>
              ) : null}
              <div aria-live="polite">
                {reviewActionError ? <p className="error-text">{reviewActionError}</p> : null}
                {reviewActionNotice ? <p className="muted">{reviewActionNotice}</p> : null}
              </div>
              {operation?.placement === "draft" ? <OperationProgress operation={operation} /> : null}
            </SectionCard>
          </>
        ) : null}

        {latestDraft ? (
          <SectionCard title="营养素草案审核与发布" subtitle="04 · Draft and publish" tone="draft">
            <p className="muted">草案 {latestDraft.id} · 状态 {latestDraft.status} · 置信度 {Math.round(latestDraft.confidence * 100)}%</p>
            {ruleExcludedDecisions.length ? (
              <details className="rule-exclusion-record">
                <summary>规则排除记录（{ruleExcludedDecisions.length}）</summary>
                <ul className="flat-list">
                  {ruleExcludedDecisions.map((decision) => (
                    <li key={`${decision.rule_id}-${decision.sku_id ?? "unknown"}`}>{decision.message}</li>
                  ))}
                </ul>
              </details>
            ) : null}
            {systemCoverageRows.length ? (
              <details className="rule-exclusion-record">
                <summary>身体系统营养素覆盖（{systemCoverageRows.length}）</summary>
                <ul className="flat-list">
                  {systemCoverageRows.map((row) => (
                    <li key={row.systemId}>
                      <strong>{row.systemName}：</strong>
                      {row.includedNames.length
                        ? `已覆盖（${row.includedNames.join("、")}）`
                        : row.excludedNames.length
                          ? `对应产品当前未纳入（${row.excludedNames.join("、")}）`
                          : UNCOVERED_SYSTEM_REASON_LABELS[row.uncoveredReason ?? ""]
                            ?? "当前证据未达到推荐条件"}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
              <div className="draft-recommendation-list">
                {latestDraft.recommended_skus.map((item) => {
                  const selectedOption = effectiveDosageOption(item, dosageOverrides);
                  const changed = Boolean(selectedOption && selectedOption.option_id !== item.dosage_option_id);
                  const reviewDecisions = (item.safety_decisions ?? []).filter(
                    (decision) => decision.action === "requires_review"
                  );
                  const warningDecisions = (item.safety_decisions ?? []).filter(
                    (decision) => decision.action === "warn"
                  );
                  return (
                    <div className="draft-recommendation-card" key={item.sku_id}>
                      <div>
                        <strong>{item.display_name}</strong>
                        {reviewDecisions.length || warningDecisions.length ? (
                          <div className="rule-badge-list">
                            {reviewDecisions.length ? <span className="rule-badge rule-badge--review">需医生确认</span> : null}
                            {warningDecisions.length ? <span className="rule-badge rule-badge--warn">注意事项</span> : null}
                          </div>
                        ) : null}
                        <p className="muted">{effectiveDosageText(item, dosageOverrides)} · {item.reason}</p>
                        {(item.dosage_options?.length ?? 0) > 0 ? (
                          <div className="dosage-review-fields">
                            <label className="field">
                              <span>批准剂量档位</span>
                              <select
                                value={selectedOption?.option_id ?? item.dosage_option_id ?? ""}
                                disabled={busy || Boolean(payload.review_decision)}
                                onChange={(event) => setDosageOverrides((current) => ({
                                  ...current,
                                  [item.sku_id]: {
                                    option_id: event.target.value,
                                    note: current[item.sku_id]?.note ?? ""
                                  }
                                }))}
                              >
                                {item.dosage_options?.map((option) => (
                                  <option value={option.option_id} key={option.option_id}>{option.label}</option>
                                ))}
                              </select>
                            </label>
                            {changed ? (
                              <label className="field">
                                <span>调整备注（必填）</span>
                                <textarea
                                  rows={2}
                                  value={dosageOverrides[item.sku_id]?.note ?? ""}
                                  disabled={busy || Boolean(payload.review_decision)}
                                  onChange={(event) => setDosageOverrides((current) => ({
                                    ...current,
                                    [item.sku_id]: {
                                      option_id: current[item.sku_id]?.option_id ?? item.dosage_option_id ?? "",
                                      note: event.target.value
                                    }
                                  }))}
                                  placeholder="请说明人工改档依据"
                                />
                              </label>
                            ) : null}
                          </div>
                        ) : null}
                        {(item.covered_system_ids?.length || item.primary_system_id) ? (
                          <p className="muted">
                            对应身体系统：{(
                              item.covered_system_ids?.length
                                ? item.covered_system_ids
                                : item.primary_system_id
                                  ? [item.primary_system_id]
                                  : []
                            ).map((systemId) => BODY_SYSTEM_LABELS[systemId] ?? systemId).join("、")}
                          </p>
                        ) : null}
                        {item.evidence_details.length ? <p className="muted">{item.evidence_details.join("；")}</p> : null}
                        {item.warnings.length ? <p className="error-text">{item.warnings.join("；")}</p> : null}
                      </div>
                      <label>
                        <input
                          type="checkbox"
                          checked={!excludedSkuIds.includes(item.sku_id)}
                          onChange={(event) => setExcludedSkuIds((current) => event.target.checked ? current.filter((id) => id !== item.sku_id) : [...current, item.sku_id])}
                        /> 纳入
                      </label>
                    </div>
                  );
                })}
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
                className="publishable-editor-textarea publishable-editor-textarea--legacy"
                rows={18}
                value={publishableSummary}
                onChange={(event) => setPublishableSummary(event.target.value)}
                aria-label="最终发布内容"
              />
              <MarkdownEditor value={publishableSummary} onChange={setPublishableSummary} mode={publishableEditorMode} onModeChange={setPublishableEditorMode} />
            </div>
            <div className="button-row">
              <button type="button" className="primary-button" disabled={busy || Boolean(payload.review_decision) || includedRecommendationCount === 0} onClick={() => void handleApprove()}>{payload.review_decision ? "已审核发布" : "审核并发布"}</button>
              {payload.review_decision ? <button type="button" className="secondary-button" disabled={busy} onClick={() => void handleDownloadReport(latestDraft.id)}>下载 PDF</button> : null}
            </div>
            {operation?.placement === "report" ? <OperationProgress operation={operation} /> : null}
          </SectionCard>
        ) : null}

        <SectionCard title="审计记录" subtitle="05 · Audit trail" className="audit-section">
          <details>
            <summary>查看最近 {Math.min(payload.audit_logs.length, 20)} 条操作记录</summary>
            <div className="stack audit-section__body">{payload.audit_logs.slice(0, 20).map((log) => <div className="file-row" key={log.id}><div><strong>{log.action}</strong><p className="muted">{log.actor_id} · {new Date(log.created_at).toLocaleString("zh-CN")}</p></div></div>)}</div>
          </details>
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
              className="report-editor-dialog__textarea report-editor-dialog__textarea--legacy"
              value={publishableSummary}
              onChange={(event) => setPublishableSummary(event.target.value)}
              aria-label="放大编辑最终发布内容"
            />
            <MarkdownEditor value={publishableSummary} onChange={setPublishableSummary} mode={publishableEditorMode} onModeChange={setPublishableEditorMode} expanded />
          </div>
        </div>
      ) : null}
    </div>
  );
}
