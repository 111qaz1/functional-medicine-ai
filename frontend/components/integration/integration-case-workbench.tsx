"use client";

import React, { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { buildApprovalRequest, createApprovalDraft, type ApprovalDraftState } from "../../lib/api-v2/approval";
import {
  analysisStatusLabels,
  caseStatusLabels,
  draftGenerationStatusLabels,
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
  AttachmentType,
  CaseResponse,
  DraftResponse,
  OperationResponse,
  ReportResponse
} from "../../lib/api-v2/types";
import { currentWorkflowStep, deriveWorkflowSteps, type WorkflowStepId } from "../../lib/api-v2/workflow-state";
import { DraftApproval } from "./draft-approval";
import { ReviewEditor } from "./review-editor";
import { WorkflowOperation } from "./workflow-operation";
import { WorkflowNotice, WorkflowSection, WorkflowShell } from "./workflow-shell";

type LoadState = "loading" | "ready" | "error";

function sectionState(steps: ReturnType<typeof deriveWorkflowSteps>, id: WorkflowStepId) {
  return steps.find((step) => step.id === id)?.state ?? "blocked";
}

function attachmentStatusLabel(status: string): string {
  return ({
    parsed: "解析完成",
    pending: "等待解析",
    questionnaire_imported: "问卷已导入",
    duplicate: "重复文件",
    failed: "处理失败"
  } as Record<string, string>)[status] ?? status;
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
  const pollerRef = useRef<OperationPoller | null>(null);
  const loadSequence = useRef(0);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [caseResource, setCaseResource] = useState<CaseResponse | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [clinicalSummary, setClinicalSummary] = useState("");
  const [reviewDraft, setReviewDraft] = useState<ReviewDraftState | null>(null);
  const [reviewBaseline, setReviewBaseline] = useState("");
  const [reviewerId, setReviewerId] = useState("");
  const [approvalDraft, setApprovalDraft] = useState<ApprovalDraftState | null>(null);
  const [approvalBaseline, setApprovalBaseline] = useState("");
  const [operation, setOperation] = useState<OperationResponse | null>(null);
  const [autoPolling, setAutoPolling] = useState(false);
  const [attachmentResults, setAttachmentResults] = useState<Partial<Record<AttachmentType, AttachmentBatchResponse>>>({});
  const [thirdPartyConfirmed, setThirdPartyConfirmed] = useState(false);
  const [action, setAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reviewConflict, setReviewConflict] = useState(false);

  const reviewDirty = Boolean(reviewDraft && reviewBaseline && JSON.stringify(reviewDraft) !== reviewBaseline);
  const approvalDirty = Boolean(approvalDraft && approvalBaseline && JSON.stringify(approvalDraft) !== approvalBaseline);
  const summaryDirty = clinicalSummary !== (caseResource?.clinical_summary ?? "");
  const operationBusy = autoPolling && (operation?.status === "queued" || operation?.status === "running");
  const busy = Boolean(action) || operationBusy;

  const loadWorkflow = useCallback(async (discardLocalReview: boolean) => {
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
      if (loadedAnalysis?.draft_id) {
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
      setClinicalSummary(loadedCase.clinical_summary ?? "");
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
        if (event.reason === "terminal") void loadWorkflow(true);
      }
    );
  }, [loadWorkflow]);

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
  const currentStep = currentWorkflowStep(steps);

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
      const updated = await gateway.updateClinicalSummary(caseId, {
        clinical_summary: clinicalSummary.trim() || null
      });
      setCaseResource(updated);
      setClinicalSummary(updated.clinical_summary ?? "");
      setNotice("临床摘要已保存。");
    } catch (cause) {
      setError(workflowErrorMessage(cause));
    } finally {
      setAction(null);
    }
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>, attachmentType: AttachmentType) {
    event.preventDefault();
    if (!confirmDiscardEdits()) return;
    const form = event.currentTarget;
    const files = new FormData(form).getAll("files").filter((item): item is File => item instanceof File && item.size > 0);
    if (!files.length) {
      setError("请选择至少一个有效文件。");
      return;
    }
    setAction(`upload-${attachmentType}`);
    setError(null);
    try {
      const result = await gateway.uploadAttachments(caseId, attachmentType, files);
      setAttachmentResults((current) => ({ ...current, [attachmentType]: result }));
      form.reset();
      await loadWorkflow(true);
    } catch (cause) {
      setError(workflowErrorMessage(cause));
    } finally {
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
        reviewer_id: reviewerId.trim(),
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
      setError(workflowErrorMessage(cause));
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
    } catch (cause) {
      setError(workflowErrorMessage(cause));
    } finally {
      setAction(null);
    }
  }

  async function handleCheckReport() {
    if (!draft) return;
    setAction("report-status");
    setError(null);
    try {
      setReport(await gateway.getReport(draft.id));
      setNotice("报告已就绪。");
    } catch (cause) {
      if (isWorkflowProblem(cause, "REPORT_NOT_READY", "REPORT_NOT_FOUND")) {
        setReport(null);
        setNotice("报告尚未就绪，请稍后重新检查。");
      } else {
        setError(workflowErrorMessage(cause));
      }
    } finally {
      setAction(null);
    }
  }

  async function handleDownloadReport() {
    if (!draft) return;
    setAction("report-download");
    setError(null);
    try {
      const readyReport = await gateway.getReport(draft.id);
      setReport(readyReport);
      const downloaded = await gateway.downloadReport(draft.id);
      const url = URL.createObjectURL(downloaded.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = downloaded.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
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
      currentStep={currentStep}
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
          <a className="workflow-button workflow-button--secondary" href="/integration/cases">返回病例入口</a>
          <button className="workflow-button workflow-button--secondary" type="button" disabled={busy} onClick={() => { if (confirmDiscardEdits()) void loadWorkflow(true); }}>重新加载全部状态</button>
        </>
      }
    >
      {fixtureMode ? <WorkflowNotice tone="warning">Fixture 模式已启用，当前场景：{fixtureScenario}。页面不会调用后端或模型。</WorkflowNotice> : null}
      {error ? <WorkflowNotice tone="error">{error}</WorkflowNotice> : null}
      {notice ? <WorkflowNotice tone="info" live>{notice}</WorkflowNotice> : null}

      <WorkflowSection id="case" title="病例信息与临床摘要" description="临床摘要可更新或清空，病例基本字段保持只读。" state={sectionState(steps, "case")}>
        <dl className="workflow-definition-grid">
          <div><dt>客户名称</dt><dd>{caseResource.customer_name}</dd></div>
          <div><dt>顾问 ID</dt><dd>{caseResource.consultant_id ?? "未填写"}</dd></div>
          <div><dt>备注</dt><dd>{caseResource.notes ?? "未填写"}</dd></div>
          <div><dt>更新时间</dt><dd>{new Date(caseResource.updated_at).toLocaleString("zh-CN")}</dd></div>
        </dl>
        <label className="workflow-field">
          <span>临床摘要</span>
          <textarea rows={6} maxLength={20000} value={clinicalSummary} onChange={(event) => setClinicalSummary(event.target.value)} />
        </label>
        <button className="workflow-button workflow-button--primary" type="button" disabled={busy} onClick={() => void handleSaveClinicalSummary()}>
          {action === "summary" ? "正在保存…" : "保存临床摘要"}
        </button>
      </WorkflowSection>

      <WorkflowSection id="attachments" title="病例资料" description="病历与问卷分开提交；批次内单个文件失败不会回滚其他文件。" state={sectionState(steps, "attachments")}>
        <div className="workflow-upload-grid">
          {(["medical_record", "questionnaire"] as AttachmentType[]).map((type) => (
            <form className="workflow-upload-panel" key={type} onSubmit={(event) => void handleUpload(event, type)}>
              <div>
                <h3>{type === "medical_record" ? "上传病历资料" : "上传问卷资料"}</h3>
                <p>{type === "medical_record" ? "用于检查结果、报告与补充说明。" : "按问卷附件类型导入，不与病历入口混用。"}</p>
              </div>
              <label className="workflow-field">
                <span>选择文件</span>
                <input name="files" type="file" multiple required disabled={busy} />
              </label>
              <button className="workflow-button workflow-button--secondary" type="submit" disabled={busy}>
                {action === `upload-${type}` ? "正在上传…" : type === "medical_record" ? "上传病历" : "上传问卷"}
              </button>
              {attachmentResults[type] ? (
                <ul className="workflow-upload-results" aria-live="polite">
                  {attachmentResults[type]?.items.map((item, index) => (
                    <li key={`${item.filename}-${index}`} data-state={item.status}>
                      <strong>{item.filename}</strong>
                      <span>{attachmentStatusLabel(item.status)}</span>
                      {item.failure ? <small>{item.failure.message}</small> : null}
                      {item.warnings.map((warning) => <small key={warning}>{warning}</small>)}
                    </li>
                  ))}
                </ul>
              ) : null}
            </form>
          ))}
        </div>
        {caseResource.attachments.length ? (
          <div className="workflow-attachment-list">
            <h3>已接收病历附件</h3>
            <ul>
              {caseResource.attachments.map((item) => (
                <li key={item.id} data-state={item.parse_status}>
                  <div><strong>{item.filename}</strong><small>{item.media_type}，{item.size_bytes.toLocaleString("zh-CN")} 字节</small></div>
                  <span className="workflow-status-badge">
                    {attachmentStatusLabel(item.parse_status)}{item.needs_manual_review ? "，需人工复核" : ""}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : <p className="workflow-empty">尚未从病例资源读取到病历附件；问卷批次结果以上传返回为准。</p>}
      </WorkflowSection>

      <WorkflowSection id="analysis" title="综合分析" description="确认第三方处理后启动，Operation 失败通过业务状态展示。" state={sectionState(steps, "analysis")}>
        {analysis ? (
          <div className="workflow-stack">
            <div className="workflow-status-line" data-state={analysis.status}>
              <strong>{analysisStatusLabels[analysis.status]}</strong>
              <span>{analysis.progress.percent}%</span>
            </div>
            {analysis.case_summary ? <p className="workflow-prose">{analysis.case_summary}</p> : null}
            {analysis.system_findings.length ? <ul>{analysis.system_findings.map((item, index) => <li key={index}>{item}</li>)}</ul> : null}
            {analysis.warnings.map((warning) => <WorkflowNotice tone="warning" key={warning}>{warning}</WorkflowNotice>)}
            {analysis.error ? <WorkflowNotice tone="error">{analysis.error.message}（{analysis.error.code}）</WorkflowNotice> : null}
            <p>{draftGenerationStatusLabels[analysis.draft_generation.status]}，{analysis.draft_generation.progress}%</p>
            {analysis.draft_generation.status === "failed" ? (
              <button className="workflow-button workflow-button--secondary" type="button" disabled={busy} onClick={() => void handleRetryDraft()}>
                {action === "retry-draft" ? "正在重试…" : "重试草案生成"}
              </button>
            ) : null}
          </div>
        ) : (
          <div className="workflow-stack">
            <label className="workflow-check">
              <input type="checkbox" checked={thirdPartyConfirmed} onChange={(event) => setThirdPartyConfirmed(event.target.checked)} />
              <span>已确认本病例资料可发送至已配置的第三方模型服务处理</span>
            </label>
            <button className="workflow-button workflow-button--primary" type="button" disabled={busy || !thirdPartyConfirmed || sectionState(steps, "analysis") === "blocked"} onClick={() => void handleStartAnalysis()}>
              {action === "analysis" ? "正在启动…" : "启动综合分析"}
            </button>
          </div>
        )}
        {operation?.stage === "analysis" ? (
          <WorkflowOperation
            operation={operation}
            polling={autoPolling}
            onStopPolling={() => {
              pollerRef.current?.cancel(false);
              setAutoPolling(false);
              setNotice("已停止自动轮询，服务端任务不会被取消。");
            }}
            onResumePolling={() => trackOperation({ operation, location: null })}
          />
        ) : null}
      </WorkflowSection>

      <WorkflowSection id="review" title="医生差量复核" description="本地编辑只转换为 add、update、remove 差量，未修改字段不发送。" state={sectionState(steps, "review")}>
        {analysis && reviewDraft && (analysis.status === "ready_for_review" || analysis.status === "reviewed") ? (
          <ReviewEditor
            analysis={analysis}
            value={reviewDraft}
            onChange={setReviewDraft}
            reviewerId={reviewerId}
            onReviewerIdChange={setReviewerId}
            sourceOptions={sourceOptions}
            busy={busy}
            conflict={reviewConflict}
            onSubmit={() => void handleReview()}
            onDiscardAndReload={() => void loadWorkflow(true)}
          />
        ) : <p className="workflow-empty">分析完成后可进行医生复核。</p>}
        {operation?.stage === "draft_generation" ? (
          <WorkflowOperation
            operation={operation}
            polling={autoPolling}
            onStopPolling={() => {
              pollerRef.current?.cancel(false);
              setAutoPolling(false);
              setNotice("已停止自动轮询，服务端任务不会被取消。");
            }}
            onResumePolling={() => trackOperation({ operation, location: null })}
          />
        ) : null}
      </WorkflowSection>

      <WorkflowSection id="draft" title="草案审批" description="仅提交排除项、发生变化的剂量，以及医生主动开启的公开摘要覆盖。" state={sectionState(steps, "draft")}>
        {draft && approvalDraft ? (
          <DraftApproval draft={draft} value={approvalDraft} onChange={setApprovalDraft} busy={busy} onApprove={() => void handleApprove()} />
        ) : <p className="workflow-empty">完成复核并生成草案后可审批。</p>}
      </WorkflowSection>

      <WorkflowSection id="report" title="报告下载" description="先确认报告资源状态，再请求 PDF，并保留服务端文件名。" state={sectionState(steps, "report")}>
        {draft?.status === "approved" ? (
          <div className="workflow-stack">
            {report ? (
              <WorkflowNotice tone="success">报告已就绪：{report.filename}</WorkflowNotice>
            ) : (
              <WorkflowNotice tone="info">审批已完成，报告状态尚未确认。</WorkflowNotice>
            )}
            <div className="workflow-action-row">
              <button className="workflow-button workflow-button--secondary" type="button" disabled={busy} onClick={() => void handleCheckReport()}>
                {action === "report-status" ? "正在检查…" : "检查报告状态"}
              </button>
              <button className="workflow-button workflow-button--primary" type="button" disabled={busy} onClick={() => void handleDownloadReport()}>
                {action === "report-download" ? "正在下载…" : "下载 PDF"}
              </button>
            </div>
          </div>
        ) : <p className="workflow-empty">草案审批发布后可获取报告。</p>}
      </WorkflowSection>
    </WorkflowShell>
  );
}
