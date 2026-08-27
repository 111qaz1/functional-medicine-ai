"use client";

import React, { type ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeftIcon,
  ArrowPathIcon,
  ArrowRightStartOnRectangleIcon
} from "@heroicons/react/24/outline";

import { buildApprovalRequest, createApprovalDraft, type ApprovalDraftState } from "../../lib/api-v2/approval";
import {
  analysisStatusLabels,
  caseStatusLabels,
  workflowCopy
} from "../../lib/api-v2/copy";
import { isWorkflowProblem, workflowErrorMessage } from "../../lib/api-v2/errors";
import type { FixtureScenario } from "../../lib/api-v2/fixture-gateway";
import type { AcceptedOperation } from "../../lib/api-v2/gateway";
import { createWorkflowGateway } from "../../lib/api-v2/gateway-factory";
import { OperationPoller } from "../../lib/api-v2/operation-poller";
import { buildReviewChanges, createReviewDraft, type ReviewDraftState } from "../../lib/api-v2/review-diff";
import type {
  AnalysisResponse,
  AttachmentBatchResponse,
  CaseResponse,
  DraftResponse,
  OperationResponse,
  ReportResponse
} from "../../lib/api-v2/types";
import {
  currentWorkflowStep,
  deriveWorkflowSteps,
  resolveDraftCompletionNavigation,
  resolveRequestedWorkflowStep,
  type WorkflowStepId
} from "../../lib/api-v2/workflow-state";
import { DraftApproval } from "./draft-approval";
import { FinalReportEditor } from "./final-report-editor";
import { OperationProgress, type OperationProgressState } from "../operation-progress";
import { useIntegrationDoctor } from "./doctor-session";
import { ReviewEditor } from "./review-editor";
import { WorkflowNotice, WorkflowSection, WorkflowShell } from "./workflow-shell";

type LoadState = "loading" | "ready" | "error";

function sectionState(steps: ReturnType<typeof deriveWorkflowSteps>, id: WorkflowStepId) {
  return steps.find((step) => step.id === id)?.state ?? "blocked";
}

function attachmentStatusLabel(status: string): string {
  return ({
    parsed: "预解析完成",
    pending: "等待综合分析处理",
    questionnaire_imported: "问卷已导入",
    duplicate: "重复文件",
    failed: "处理失败"
  } as Record<string, string>)[status] ?? status;
}

function attachmentBatchCounts(result: AttachmentBatchResponse) {
  const duplicate = result.items.filter((item) => item.status === "duplicate").length;
  const failed = result.items.filter((item) => item.status === "failed").length;
  return {
    success: result.items.length - duplicate - failed,
    duplicate,
    failed
  };
}

function operationProgressState(operation: OperationResponse): OperationProgressState {
  const failed = operation.status === "failed";
  return {
    placement: operation.stage === "analysis" ? "analysis" : "draft",
    title: operation.stage === "analysis" ? "综合病例分析" : "生成结构化草案",
    stage: failed
      ? operation.failure?.message ?? "工作流执行失败"
      : operation.progress.current_item ?? (operation.status === "queued" ? "任务已排队" : "正在更新任务状态"),
    percent: operation.progress.percent,
    status: failed ? "error" : operation.status === "succeeded" ? "success" : "running"
  };
}

export function IntegrationCaseWorkbench({
  caseId,
  fixtureMode,
  fixtureScenario
}: {
  caseId: string;
  fixtureMode: boolean;
  fixtureScenario: FixtureScenario;
}) {
  const gateway = useMemo(
    () => createWorkflowGateway(fixtureMode, fixtureScenario),
    [fixtureMode, fixtureScenario]
  );
  const { doctor, logout } = useIntegrationDoctor();
  const pollerRef = useRef<OperationPoller | null>(null);
  const loadSequence = useRef(0);
  const handledDraftCompletion = useRef<string | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [caseResource, setCaseResource] = useState<CaseResponse | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [clinicalSummary, setClinicalSummary] = useState("");
  const [reviewDraft, setReviewDraft] = useState<ReviewDraftState | null>(null);
  const [reviewBaseline, setReviewBaseline] = useState("");
  const [approvalDraft, setApprovalDraft] = useState<ApprovalDraftState | null>(null);
  const [approvalBaseline, setApprovalBaseline] = useState("");
  const [operation, setOperation] = useState<OperationResponse | null>(null);
  const [autoPolling, setAutoPolling] = useState(false);
  const [attachmentResults, setAttachmentResults] = useState<AttachmentBatchResponse | null>(null);
  const [uploadingFileCount, setUploadingFileCount] = useState(0);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [visibleStep, setVisibleStep] = useState<WorkflowStepId>("case");
  const [thirdPartyConfirmed, setThirdPartyConfirmed] = useState(false);
  const [action, setAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reviewConflict, setReviewConflict] = useState(false);

  const reviewDirty = Boolean(reviewDraft && reviewBaseline && JSON.stringify(reviewDraft) !== reviewBaseline);
  const approvalDirty = Boolean(approvalDraft && approvalBaseline && JSON.stringify(approvalDraft) !== approvalBaseline);
  const summaryDirty = clinicalSummary !== (caseResource?.clinical_summary ?? "");
  const analysisRunning = Boolean(analysis && ["queued", "preparing", "analyzing_documents", "synthesizing", "validating"].includes(analysis.status));
  const analysisReady = analysis?.status === "ready_for_review" || analysis?.status === "reviewed";
  const analysisRestartable = analysis?.status === "failed" || analysis?.status === "stale";
  const analysisOperationVisible = operation?.stage === "analysis" && operation.status !== "failed" && !analysisReady;
  const operationBusy = autoPolling && (operation?.status === "queued" || operation?.status === "running");
  const busy = Boolean(action) || operationBusy;
  const uploadCounts = attachmentResults ? attachmentBatchCounts(attachmentResults) : null;

  const loadWorkflow = useCallback(async (discardLocalReview: boolean, preserveClinicalSummary = false) => {
    const sequence = ++loadSequence.current;
    setLoadState("loading");
    setError(null);
    try {
      const loadedCase = await gateway.getCase(caseId);
      let loadedAnalysis: AnalysisResponse | null = null;
      let loadedDraft: DraftResponse | null = null;
      let loadedReport: ReportResponse | null = null;
      try {
        loadedAnalysis = await gateway.getLatestAnalysis(caseId);
      } catch (cause) {
        if (!isWorkflowProblem(cause, "ANALYSIS_NOT_FOUND")) throw cause;
      }
      if (loadedAnalysis?.draft_id && loadedAnalysis.status !== "stale" && loadedAnalysis.status !== "failed") {
        try {
          loadedDraft = await gateway.getDraft(loadedAnalysis.draft_id);
        } catch (cause) {
          if (!isWorkflowProblem(cause, "DRAFT_NOT_FOUND")) throw cause;
        }
      }
      if (loadedDraft?.status === "approved") {
        try {
          loadedReport = await gateway.getReport(loadedDraft.id);
        } catch (cause) {
          if (!isWorkflowProblem(cause, "REPORT_NOT_READY", "REPORT_NOT_FOUND")) throw cause;
        }
      }
      if (sequence !== loadSequence.current) return;
      setCaseResource(loadedCase);
      if (!preserveClinicalSummary) setClinicalSummary(loadedCase.clinical_summary ?? "");
      setAnalysis(loadedAnalysis);
      setDraft(loadedDraft);
      setReport(loadedReport);
      if (discardLocalReview) {
        const nextReview = loadedAnalysis ? createReviewDraft(loadedAnalysis) : null;
        setReviewDraft(nextReview);
        setReviewBaseline(nextReview ? JSON.stringify(nextReview) : "");
        setReviewConflict(false);
      }
      if (loadedDraft) {
        const nextApproval = createApprovalDraft(loadedDraft);
        if (loadedReport?.publishable_report) nextApproval.publishableReport = loadedReport.publishable_report;
        setApprovalDraft(nextApproval);
        setApprovalBaseline(JSON.stringify(nextApproval));
      } else {
        setApprovalDraft(null);
        setApprovalBaseline("");
      }
      setLoadState("ready");
    } catch (cause) {
      if (sequence !== loadSequence.current) return;
      setError(workflowErrorMessage(cause));
      setLoadState("error");
    }
  }, [caseId, gateway]);

  useEffect(() => {
    const poller = new OperationPoller(gateway);
    pollerRef.current = poller;
    void loadWorkflow(true);
    return () => {
      poller.cancel(false);
      pollerRef.current = null;
    };
  }, [gateway, loadWorkflow]);

  useEffect(() => {
    function warnBeforeUnload(event: BeforeUnloadEvent) {
      if (!(reviewDirty || approvalDirty || summaryDirty)) return;
      event.preventDefault();
    }
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [approvalDirty, reviewDirty, summaryDirty]);

  const trackOperation = useCallback((accepted: AcceptedOperation) => {
    setOperation(accepted.operation);
    setAutoPolling(true);
    setNotice(null);
    pollerRef.current?.start(
      accepted.operation.operation_id,
      setOperation,
      (event) => {
        if (event.reason !== "cancelled") setAutoPolling(false);
        if (event.operation) setOperation(event.operation);
        if (event.reason === "timeout") {
          setNotice(workflowCopy.common.pollTimeout);
          return;
        }
        if (event.reason === "request_error") {
          setError(workflowErrorMessage(event.error));
          return;
        }
        if (event.reason === "terminal") void loadWorkflow(true, summaryDirty);
      }
    );
  }, [loadWorkflow, summaryDirty]);

  useEffect(() => {
    if (!analysis || operation) return;
    const analysisRunning = ["queued", "preparing", "analyzing_documents", "synthesizing", "validating"].includes(analysis.status);
    const draftRunning = [
      "queued",
      "final_synthesizing",
      "validating_support_needs",
      "mapping_products",
      "checking_safety",
      "generating_draft"
    ].includes(analysis.draft_generation.status);
    if (!analysisRunning && !draftRunning) return;
    let active = true;
    void gateway.getOperation(analysis.id).then((recovered) => {
      if (active && (recovered.status === "queued" || recovered.status === "running")) {
        trackOperation({ operation: recovered, location: null });
      }
    }).catch((cause) => {
      if (active) setError(workflowErrorMessage(cause));
    });
    return () => { active = false; };
  }, [analysis, gateway, operation, trackOperation]);

  const steps = deriveWorkflowSteps({ caseResource, analysis, draft, report });
  const businessStep = currentWorkflowStep(steps);

  const navigateToStep = useCallback((nextStep: WorkflowStepId, replace = false) => {
    const target = steps.find((step) => step.id === nextStep);
    if (!target || target.state === "blocked") return;
    setVisibleStep(nextStep);
    const url = new URL(window.location.href);
    url.searchParams.set("step", nextStep);
    window.history[replace ? "replaceState" : "pushState"]({}, "", url);
  }, [steps]);

  useEffect(() => {
    if (!caseResource) return;
    const restoreStep = () => {
      const rawRequested = new URL(window.location.href).searchParams.get("step");
      const nextStep = resolveRequestedWorkflowStep(rawRequested, steps, businessStep, analysisReady);
      setVisibleStep(nextStep);
      if (rawRequested !== nextStep) {
        const url = new URL(window.location.href);
        url.searchParams.set("step", nextStep);
        window.history.replaceState({}, "", url);
      }
    };
    restoreStep();
    window.addEventListener("popstate", restoreStep);
    return () => window.removeEventListener("popstate", restoreStep);
  }, [analysisReady, businessStep, caseResource, steps]);

  useEffect(() => {
    if (operation?.stage === "analysis" && operation.status === "succeeded" && analysisReady && visibleStep === "attachments") {
      navigateToStep("review", true);
    }
  }, [analysisReady, navigateToStep, operation, visibleStep]);

  useEffect(() => {
    const resolution = resolveDraftCompletionNavigation(
      operation,
      Boolean(draft),
      visibleStep,
      handledDraftCompletion.current
    );
    if (!resolution) return;
    handledDraftCompletion.current = resolution.operationId;
    if (resolution.nextStep) navigateToStep(resolution.nextStep, true);
  }, [draft, navigateToStep, operation, visibleStep]);

  const sourceOptions = useMemo(() => {
    const sources = new Map<string, string>();
    for (const item of caseResource?.attachments ?? []) sources.set(item.id, item.filename);
    for (const item of analysis?.abnormal_findings ?? []) sources.set(item.source_file_id, item.source_file_name);
    if (analysis?.food_sensitivity) {
      sources.set(analysis.food_sensitivity.source_file_id, analysis.food_sensitivity.source_file_name);
    }
    return [...sources].map(([id, name]) => ({ id, name }));
  }, [analysis, caseResource]);

  function confirmDiscardEdits(): boolean {
    return !(reviewDirty || approvalDirty || summaryDirty) || window.confirm("重新加载会丢弃尚未保存的页面编辑，是否继续？");
  }

  async function handleSaveClinicalSummary() {
    setAction("summary");
    setError(null);
    try {
      await gateway.updateClinicalSummary(caseId, {
        clinical_summary: clinicalSummary.trim() || null
      });
      await loadWorkflow(true);
      setNotice("医生病例总结已保存；病例资料变化可能使此前分析或方案过期，需要时请重新分析。");
    } catch (cause) {
      setError(workflowErrorMessage(cause));
    } finally {
      setAction(null);
    }
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const files = Array.from(input.files ?? []).filter((file) => file.size > 0);
    if (!files.length) {
      input.value = "";
      return;
    }

    const preserveClinicalSummary = summaryDirty;
    setAction("upload");
    setUploadingFileCount(files.length);
    setAttachmentResults(null);
    setUploadError(null);
    setError(null);
    try {
      const result = await gateway.uploadAttachments(caseId, "medical_record", files);
      setAttachmentResults(result);
      await loadWorkflow(true, preserveClinicalSummary);
    } catch (cause) {
      setUploadError(workflowErrorMessage(cause));
    } finally {
      input.value = "";
      setUploadingFileCount(0);
      setAction(null);
    }
  }

  async function handleStartAnalysis() {
    setAction("analysis");
    setError(null);
    try {
      trackOperation(await gateway.startAnalysis(caseId, {
        third_party_processing_confirmed: thirdPartyConfirmed
      }));
    } catch (cause) {
      setError(workflowErrorMessage(cause));
    } finally {
      setAction(null);
    }
  }

  async function handleReview() {
    if (!analysis || !reviewDraft) return;
    setAction("review");
    setError(null);
    setReviewConflict(false);
    try {
      const changes = buildReviewChanges(analysis, reviewDraft);
      trackOperation(await gateway.submitReview(caseId, analysis.id, {
        expected_revision: analysis.revision,
        ...changes
      }));
    } catch (cause) {
      if (isWorkflowProblem(cause, "ANALYSIS_REVISION_CONFLICT")) {
        setReviewConflict(true);
      } else {
        setError(workflowErrorMessage(cause));
      }
    } finally {
      setAction(null);
    }
  }

  async function handleRetryDraft() {
    if (!analysis) return;
    setAction("retry-draft");
    setError(null);
    try {
      trackOperation(await gateway.retryDraftGeneration(caseId, analysis.id));
    } catch (cause) {
      if (isWorkflowProblem(cause, "DRAFT_REVISION_CONFLICT", "DRAFT_STALE")) {
        setError("草案或病例资料已更新。当前页面编辑已保留，请重新加载最新版本后再次确认。");
      } else {
        setError(workflowErrorMessage(cause));
      }
    } finally {
      setAction(null);
    }
  }

  async function handleApprove() {
    if (!draft || !approvalDraft) return;
    setAction("approval");
    setError(null);
    try {
      const approved = await gateway.approveDraft(draft.id, buildApprovalRequest(draft, approvalDraft));
      setNotice(approved.report_ready ? "审批完成，报告已可下载。" : "审批完成，正在等待报告生成。");
      await loadWorkflow(true);
      navigateToStep("report", true);
      if (approved.report_ready) {
        const readyReport = await gateway.getReport(draft.id);
        setReport(readyReport);
        await downloadReportFile(draft.id);
      }
    } catch (cause) {
      if (isWorkflowProblem(cause, "DRAFT_REVISION_CONFLICT", "DRAFT_STALE")) {
        setError("草案或病例资料已经更新。当前审批编辑已保留，请重新加载最新版本后再次确认。");
      } else {
        setError(workflowErrorMessage(cause));
      }
    } finally {
      setAction(null);
    }
  }

  async function downloadReportFile(draftId: string) {
    const downloaded = await gateway.downloadReport(draftId);
    const url = URL.createObjectURL(downloaded.blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = downloaded.filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function handleDownloadReport() {
    if (!draft) return;
    setAction("report-download");
    setError(null);
    try {
      const readyReport = await gateway.getReport(draft.id);
      setReport(readyReport);
      await downloadReportFile(draft.id);
    } catch (cause) {
      setError(workflowErrorMessage(cause));
    } finally {
      setAction(null);
    }
  }

  if (!caseResource) {
    const emptySteps = deriveWorkflowSteps({ caseResource: null, analysis: null, draft: null, report: null });
    return (
      <WorkflowShell title="病例工作流" caseId={caseId} steps={emptySteps} currentStep="case">
        {fixtureMode ? <WorkflowNotice tone="warning">Fixture 模式：{fixtureScenario}</WorkflowNotice> : null}
        {error ? <WorkflowNotice tone="error">{error}</WorkflowNotice> : null}
        <WorkflowSection id="case" title={loadState === "loading" ? "正在读取病例" : "病例无法加载"} state={loadState === "error" ? "error" : "current"}>
          <p className="workflow-placeholder">{loadState === "loading" ? workflowCopy.common.loading : "请检查病例 ID、访问权限或服务端对接配置。"}</p>
          {loadState === "error" ? <button className="workflow-button workflow-button--secondary" type="button" onClick={() => void loadWorkflow(true)}>重新加载</button> : null}
        </WorkflowSection>
      </WorkflowShell>
    );
  }

  return (
    <WorkflowShell
      title={caseResource.customer_name}
      description={`病例状态：${caseStatusLabels[caseResource.status]}`}
      caseId={caseResource.id}
      steps={steps}
      currentStep={visibleStep}
      onStepChange={navigateToStep}
      contextSlot={
        <div className="workflow-case-context">
          <span>当前病例</span>
          <strong>{caseResource.customer_name}</strong>
          <small>{caseStatusLabels[caseResource.status]}</small>
          <code>{caseResource.id}</code>
        </div>
      }
      headerActions={
        <>
          <a className="workflow-button workflow-button--secondary" href="/integration/cases"><ArrowLeftIcon className="workflow-button__icon" />返回病例入口</a>
          <button className="workflow-button workflow-button--secondary" type="button" disabled={busy} onClick={() => { if (confirmDiscardEdits()) void loadWorkflow(true); }}><ArrowPathIcon className="workflow-button__icon" />重新加载全部状态</button>
          <button className="workflow-button workflow-button--secondary" type="button" disabled={busy} onClick={() => { if (confirmDiscardEdits()) void logout(); }}><ArrowRightStartOnRectangleIcon className="workflow-button__icon" />退出 {doctor.display_name}</button>
        </>
      }
    >
      {fixtureMode ? <WorkflowNotice tone="warning">Fixture 模式已启用，当前场景：{fixtureScenario}。页面不会调用后端或模型。</WorkflowNotice> : null}
      {error ? <WorkflowNotice tone="error">{error}</WorkflowNotice> : null}
      {notice ? <WorkflowNotice tone="info" live>{notice}</WorkflowNotice> : null}

      {visibleStep === "case" ? <WorkflowSection id="case" title="病例信息" description="核对病例基本字段；医生病例总结在资料步骤填写和保存。" state={sectionState(steps, "case")}>
        <dl className="workflow-definition-grid">
          <div><dt>客户名称</dt><dd>{caseResource.customer_name}</dd></div>
          <div><dt>顾问 ID</dt><dd>{caseResource.consultant_id ?? "未填写"}</dd></div>
          <div><dt>备注</dt><dd>{caseResource.notes ?? "未填写"}</dd></div>
          <div><dt>更新时间</dt><dd>{new Date(caseResource.updated_at).toLocaleString("zh-CN")}</dd></div>
        </dl>
      </WorkflowSection> : null}

      {visibleStep === "attachments" ? <WorkflowSection id="attachments" title="病例资料" description="选择文件后立即上传并预解析；医生病例总结保存后，再确认授权并开始综合分析。" state={sectionState(steps, "attachments")}>
        <div className="workflow-upload-grid">
          <label className="workflow-upload-panel" aria-disabled={busy}>
            <div>
              <h3>上传病例报告、MSQ、肠道报告、慢性食物敏感报告或总结截图</h3>
              <p>仅做轻量预检；明显无关文件会提示但不会阻止上传。默认单文件 50 MB、单个 PDF 最多 50 页。</p>
            </div>
            <input
              className="workflow-visually-hidden"
              name="files"
              type="file"
              multiple
              disabled={busy}
              aria-label="选择病例资料文件，选择后立即上传并预解析"
              onChange={(event) => void handleUpload(event)}
            />
            <span className="workflow-help">选择一个或多个文件后会立即开始上传，无需再次确认。</span>
            {action === "upload" ? (
              <WorkflowNotice tone="info" live>正在上传并预解析 {uploadingFileCount} 个文件……</WorkflowNotice>
            ) : null}
            {uploadError ? <WorkflowNotice tone="error" live>{uploadError}</WorkflowNotice> : null}
            {attachmentResults && uploadCounts ? (
              <>
                <WorkflowNotice tone={uploadCounts.failed ? "warning" : "success"} live>
                  本批处理完成：成功 {uploadCounts.success} 个，重复 {uploadCounts.duplicate} 个，失败 {uploadCounts.failed} 个。
                  {uploadCounts.failed ? "失败文件可重新选择后再次上传，已成功文件不受影响。" : ""}
                </WorkflowNotice>
                <div>
                  <h3>本批上传处理结果</h3>
                  <ul className="workflow-upload-results" aria-live="polite">
                    {attachmentResults.items.map((item, index) => (
                      <li key={`${item.filename}-${index}`} data-state={item.status}>
                        <strong>{item.filename}</strong>
                        <span>{attachmentStatusLabel(item.status)}</span>
                        {item.failure ? <small>{item.failure.message}</small> : null}
                      </li>
                    ))}
                  </ul>
                </div>
              </>
            ) : null}
          </label>
        </div>

        {caseResource.attachments.length ? (
          <div className="workflow-attachment-list">
            <h3>已接收病例资料</h3>
            <ul>
              {caseResource.attachments.map((item) => (
                <li key={item.id} data-state={item.parse_status}>
                  <div><strong>{item.filename}</strong><small>{item.media_type}，{item.size_bytes.toLocaleString("zh-CN")} 字节</small></div>
                  <span className="workflow-status-badge">
                    {attachmentStatusLabel(item.parse_status)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : <p className="workflow-empty">尚未上传病例资料。</p>}

        <div className="workflow-editor-group">
          <div className="workflow-editor-group__header">
            <div>
              <h3>医生事先填写的病例总结</h3>
              <p>该原文与模型病例总结分开保存，模型不会覆盖它。</p>
            </div>
          </div>
          <label className="workflow-field">
            <span>病例总结原文</span>
            <textarea
              rows={8}
              maxLength={20000}
              value={clinicalSummary}
              disabled={busy}
              onChange={(event) => setClinicalSummary(event.target.value)}
            />
          </label>
          {summaryDirty ? <p className="workflow-help">当前总结有未保存修改。上传资料不会覆盖这些文字。</p> : null}
          <button className="workflow-button workflow-button--secondary" type="button" disabled={busy || !summaryDirty} onClick={() => void handleSaveClinicalSummary()}>
            {action === "summary" ? "正在保存…" : "保存医生病例总结"}
          </button>
        </div>

        <div className="workflow-analysis-launch">
          <div className="workflow-analysis-launch__header">
            <h3>开始综合分析</h3>
            <p>确认资料无误后调用已配置的第三方模型服务；只有下方确认操作会启动病例级综合分析。</p>
          </div>
          {analysisReady ? (
            <div className="workflow-analysis-launch__complete">
              <WorkflowNotice tone="success">综合分析已完成，可以进入医生复核。</WorkflowNotice>
              <button className="workflow-button workflow-button--secondary" type="button" onClick={() => navigateToStep("review")}>进入医生复核</button>
            </div>
          ) : analysisRunning || analysisOperationVisible ? (
            operation?.stage === "analysis" ? (
              <OperationProgress operation={operationProgressState(operation)} />
            ) : (
              <div className="workflow-analysis-status" data-state={analysis?.status}>
                <div><small>分析状态</small><strong>{analysis ? analysisStatusLabels[analysis.status] : "正在读取分析状态"}</strong></div>
                <span>{analysis?.progress.percent ?? 0}%</span>
              </div>
            )
          ) : (
            <div className="workflow-stack">
              {analysisRestartable ? (
                analysis?.status === "stale"
                  ? <WorkflowNotice tone="warning">病例资料已发生变化，需要重新进行综合分析。</WorkflowNotice>
                  : <WorkflowNotice tone="error">{analysis?.error?.message ?? "综合分析失败，可以确认资料后重新开始。"}</WorkflowNotice>
              ) : null}
              <label className="workflow-check">
                <input type="checkbox" checked={thirdPartyConfirmed} disabled={busy} onChange={(event) => setThirdPartyConfirmed(event.target.checked)} />
                <span>已确认本病例资料可发送至已配置的第三方模型服务处理</span>
              </label>
              <button className="workflow-button workflow-button--primary" type="button" disabled={busy || !thirdPartyConfirmed || caseResource.attachments.length === 0 || Boolean(analysis && !analysisRestartable)} onClick={() => void handleStartAnalysis()}>
                {action === "analysis" ? "正在启动…" : analysisRestartable ? "重新开始综合分析" : "确认资料并开始综合分析"}
              </button>
              {operation?.stage === "analysis" && operation.status === "failed" ? <OperationProgress operation={operationProgressState(operation)} /> : null}
            </div>
          )}
        </div>
      </WorkflowSection> : null}

      {visibleStep === "review" ? <WorkflowSection id="review" title="医生复核" description="核对异常指标、当前补充剂和食敏结果，确认后生成方案草案。" state={sectionState(steps, "review")}>
        {analysis && reviewDraft && (analysis.status === "ready_for_review" || analysis.status === "reviewed") ? (
          <div className="workflow-review-content">
            <div className="workflow-analysis-overview">
              <div className="workflow-analysis-status" data-state={analysis.status}>
                <div><small>分析状态</small><strong>{analysisStatusLabels[analysis.status]}</strong></div>
                <span>{analysis.progress.percent}%</span>
              </div>
              <div className="workflow-analysis-grid">
                <article className="workflow-analysis-card workflow-analysis-card--summary">
                  <h3>病例综合摘要</h3>
                  {analysis.case_summary ? <p className="workflow-prose">{analysis.case_summary}</p> : <p className="workflow-empty">当前分析未生成病例综合摘要。</p>}
                </article>
                <article className="workflow-analysis-card">
                  <h3>系统发现</h3>
                  {analysis.system_findings.length ? <ul>{analysis.system_findings.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p className="workflow-empty">当前分析未生成独立系统发现。</p>}
                </article>
              </div>
              {analysis.warnings.map((warning) => <WorkflowNotice tone="warning" key={warning}>{warning}</WorkflowNotice>)}
              {analysis.error ? <WorkflowNotice tone="error">{analysis.error.message}（{analysis.error.code}）</WorkflowNotice> : null}
            </div>
            <ReviewEditor
              analysis={analysis}
              value={reviewDraft}
              onChange={setReviewDraft}
              reviewerName={doctor.display_name}
              sourceOptions={sourceOptions}
              busy={busy}
              conflict={reviewConflict}
              draftReady={Boolean(draft)}
              onSubmit={() => void handleReview()}
              onContinue={() => navigateToStep("draft")}
              onDiscardAndReload={() => void loadWorkflow(true)}
            />
          </div>
        ) : <p className="workflow-empty">分析完成后可进行医生复核。</p>}
        {operation?.stage === "draft_generation" ? (
          <OperationProgress operation={operationProgressState(operation)} />
        ) : null}
        {analysis?.draft_generation.status === "failed" ? (
          <div className="workflow-retry-panel">
            {operation?.stage === "draft_generation" && operation.status === "failed" ? null : (
              <WorkflowNotice tone="error">{analysis.draft_generation.error ?? "草案生成失败，可直接重试。"}</WorkflowNotice>
            )}
            <button className="workflow-button workflow-button--secondary" type="button" disabled={busy} onClick={() => void handleRetryDraft()}>
              {action === "retry-draft" ? "正在重试…" : "重试草案生成"}
            </button>
          </div>
        ) : null}
      </WorkflowSection> : null}

      {visibleStep === "draft" ? <WorkflowSection id="draft" title="方案审核" description="调整产品纳入、剂量和备注；最终报告正文在下一步统一编辑。" state={sectionState(steps, "draft")}>
        {draft && approvalDraft ? (
          <DraftApproval draft={draft} value={approvalDraft} onChange={setApprovalDraft} busy={busy} onContinue={() => navigateToStep("report")} />
        ) : <p className="workflow-empty">完成复核并生成草案后可审批。</p>}
      </WorkflowSection> : null}

      {visibleStep === "report" ? <WorkflowSection id="report" title="最终报告" description="编辑完整报告正文，确认后由当前登录医生批准并生成 PDF。" state={sectionState(steps, "report")}>
        {draft && approvalDraft ? (
          <FinalReportEditor
            draft={draft}
            value={approvalDraft}
            report={report}
            reviewerName={doctor.display_name}
            busy={busy}
            onChange={setApprovalDraft}
            onApprove={() => void handleApprove()}
            onDownload={() => void handleDownloadReport()}
          />
        ) : <p className="workflow-empty">完成复核并生成方案后可编辑最终报告。</p>}
      </WorkflowSection> : null}
    </WorkflowShell>
  );
}
