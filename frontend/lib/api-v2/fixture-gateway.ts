import type { AcceptedOperation, DownloadedReport, WorkflowGateway } from "./gateway";
import { WorkflowProblem } from "./gateway";
import type {
  AnalysisResponse,
  ApprovalRequest,
  ApprovalResponse,
  AttachmentBatchResponse,
  AttachmentResponse,
  AttachmentType,
  CaseCreateRequest,
  CaseResponse,
  ClinicalSummaryUpdateRequest,
  DraftResponse,
  FindingResponse,
  FoodSensitivityItemResponse,
  OperationResponse,
  ProblemDetails,
  ReportResponse,
  ReviewSubmitRequest,
  StartAnalysisRequest,
  SupplementResponse
} from "./types";

export const fixtureScenarios = [
  "success",
  "attachment_partial_failure",
  "analysis_failure",
  "draft_generation_failure",
  "revision_conflict",
  "approval_validation_error",
  "report_not_ready",
  "authentication_failure"
] as const;

export type FixtureScenario = typeof fixtureScenarios[number];

export function isFixtureScenario(value: string | undefined): value is FixtureScenario {
  return Boolean(value && fixtureScenarios.includes(value as FixtureScenario));
}

export interface FixtureStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

interface FixtureOperationState {
  response: OperationResponse;
  polls: number;
}

interface FixtureCaseState {
  caseResource: CaseResponse;
  analysis: AnalysisResponse | null;
  draft: DraftResponse | null;
  report: ReportResponse | null;
  operation: FixtureOperationState | null;
  draftRetried: boolean;
}

interface FixtureDatabase {
  sequence: number;
  cases: Record<string, FixtureCaseState>;
}

const STORAGE_KEY = "fm-ai-v2-workflow-fixture";
const isoNow = () => new Date().toISOString();

class MemoryFixtureStorage implements FixtureStorage {
  private readonly values = new Map<string, string>();
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
}

export function createMemoryFixtureStorage(): FixtureStorage {
  return new MemoryFixtureStorage();
}

function problem(code: string, status: number, detail: string, instance: string): WorkflowProblem {
  const titles: Record<string, string> = {
    AUTHENTICATION_REQUIRED: "Authentication required",
    CASE_NOT_FOUND: "Case not found",
    ANALYSIS_NOT_FOUND: "Analysis not found",
    DRAFT_NOT_FOUND: "Draft not found",
    ANALYSIS_REVISION_CONFLICT: "Analysis revision conflict",
    REQUEST_VALIDATION_FAILED: "Request validation failed",
    REPORT_NOT_READY: "Report not ready"
  };
  const value: ProblemDetails = {
    type: `urn:fm-ai:problem:${code.toLowerCase().replaceAll("_", "-")}`,
    title: titles[code] ?? "Fixture workflow error",
    status,
    detail,
    instance,
    code,
    errors: []
  };
  return new WorkflowProblem(value);
}

function emptyDatabase(): FixtureDatabase {
  return { sequence: 0, cases: {} };
}

function createFixtureAnalysis(caseId: string, analysisId: string): AnalysisResponse {
  const createdAt = isoNow();
  const finding: FindingResponse = {
    id: "finding_fixture_vitamin_d",
    name: "25-羟维生素 D",
    result_text: null,
    raw_value: "18",
    unit: "ng/mL",
    reference_range: "30-100",
    abnormal_flag: "low",
    interpretation: "结果低于虚构参考范围。",
    report_explanation: "此条目仅用于前端契约测试。",
    neutral_interpretation: "建议结合临床背景复核。",
    support_need_text: null,
    source_file_id: "file_fixture_medical",
    source_file_name: "fixture-medical-record.txt",
    source_page: 1,
    source_text: "25-OH Vitamin D: 18 ng/mL",
    confidence: 0.92,
    evidence_status: "verified",
    evidence_notes: [],
    observed_at: null
  };
  const supplement: SupplementResponse = {
    id: "supplement_fixture_c",
    name: "维生素 C",
    source_file_ids: ["file_fixture_medical"],
    source_file_names: ["fixture-medical-record.txt"],
    doctor_added: false
  };
  const food: FoodSensitivityItemResponse = {
    id: "food_fixture_milk",
    name: "牛奶",
    raw_value: "2",
    unit: null,
    abnormal_flag: "positive",
    severity: "moderate",
    reported_grade: "2",
    reported_grade_meaning: "虚构中度反应",
    reference_range: null,
    grading_basis: "Fixture 分级",
    source_page: 1,
    source_text: "Milk: grade 2",
    evidence_status: "verified"
  };
  return {
    id: analysisId,
    case_id: caseId,
    version: 1,
    revision: 1,
    status: "queued",
    progress: { current: 0, total: 2, percent: 0, current_file_name: null },
    case_summary: "完全虚构的病例摘要，用于验证前端工作流，不代表医学建议。",
    system_findings: ["虚构系统发现：需要医生复核维生素 D 相关信息。"],
    abnormal_findings: [finding],
    current_supplements: [supplement],
    food_sensitivity: {
      source_file_id: "file_fixture_questionnaire",
      source_file_name: "fixture-questionnaire.txt",
      source_page: 1,
      items: [food],
      interpretations: ["虚构食敏结果，仅用于界面测试。"],
      valid: true,
      warning: null
    },
    warnings: ["Fixture 数据不得用于真实医疗决策。"],
    error: null,
    draft_generation: { status: "idle", progress: 0, error: null },
    draft_id: null,
    created_at: createdAt,
    updated_at: createdAt
  };
}

function createFixtureDraft(caseId: string, draftId: string): DraftResponse {
  const regimen = {
    unit: "capsule",
    single_dose_min: 1,
    single_dose_max: 1,
    daily_frequency_min: 1,
    daily_frequency_max: 1,
    weekly_frequency_min: null,
    weekly_frequency_max: null,
    timing: ["随餐"],
    interval_hours_min: null,
    interval_hours_max: null,
    daily_max: 1,
    duration: "8 周",
    maintenance: null
  };
  return {
    id: draftId,
    case_id: caseId,
    status: "pending_review",
    revision: 1,
    public_summary: ["虚构公开摘要：建议由医生结合完整资料确认营养支持方案。"],
    key_lab_highlights: ["虚构维生素 D 结果为 18 ng/mL。"],
    recommended_skus: [
      {
        sku_id: "SKU-FIXTURE-D3",
        display_name: "虚构维生素 D3",
        dosage: "每日 1 粒",
        dosage_option_id: "default",
        dosage_option_label: "标准档",
        dosage_match_reasons: ["虚构指标匹配"],
        dosage_options: [
          { option_id: "default", label: "标准档", display_text: "每日 1 粒", requires_review: false, regimen },
          { option_id: "alternate", label: "备选档", display_text: "隔日 1 粒", requires_review: true, regimen: { ...regimen, daily_frequency_min: null, daily_frequency_max: null, weekly_frequency_min: 3, weekly_frequency_max: 4 } }
        ],
        dosage_regimen: regimen,
        reason: "基于完全虚构的指标生成，用于界面验收。",
        evidence_ids: ["finding_fixture_vitamin_d"],
        evidence_details: ["来源：fixture-medical-record.txt 第 1 页"],
        warnings: ["发布前必须由医生确认。"],
        current_supplement_overlap_notice: null
      },
      {
        sku_id: "SKU-FIXTURE-MULTI",
        display_name: "虚构复合营养素",
        dosage: "每日 1 粒",
        dosage_option_id: "default",
        dosage_option_label: "标准档",
        dosage_match_reasons: ["虚构生活方式支持"],
        dosage_options: [
          { option_id: "default", label: "标准档", display_text: "每日 1 粒", requires_review: false, regimen }
        ],
        dosage_regimen: regimen,
        reason: "用于测试多 SKU 排除校验。",
        evidence_ids: [],
        evidence_details: [],
        warnings: [],
        current_supplement_overlap_notice: "请核对患者当前补充剂。"
      }
    ],
    lifestyle_actions: ["保持规律作息并记录虚构执行情况。"],
    rationale: ["所有内容均为 Fixture。"],
    evidence_details: ["不包含真实患者数据。"],
    contraindications: ["真实使用前必须重新评估禁忌。"],
    missing_info: ["真实用药史未提供。"],
    confidence: 0.78,
    abstain_reason: null,
    manual_review_required: true,
    red_flags: [],
    generated_at: isoNow()
  };
}

export class FixtureWorkflowGateway implements WorkflowGateway {
  private readonly storage: FixtureStorage;

  constructor(
    private readonly scenario: FixtureScenario = "success",
    storage?: FixtureStorage
  ) {
    this.storage = storage ?? (typeof sessionStorage === "undefined" ? createMemoryFixtureStorage() : sessionStorage);
  }

  private authenticate(instance: string) {
    if (this.scenario === "authentication_failure") {
      throw problem("AUTHENTICATION_REQUIRED", 401, "Fixture 模拟：访问令牌无效。", instance);
    }
  }

  private read(): FixtureDatabase {
    const stored = this.storage.getItem(STORAGE_KEY);
    if (!stored) return emptyDatabase();
    try { return JSON.parse(stored) as FixtureDatabase; } catch { return emptyDatabase(); }
  }

  private write(database: FixtureDatabase) {
    this.storage.setItem(STORAGE_KEY, JSON.stringify(database));
  }

  private caseState(database: FixtureDatabase, caseId: string, instance: string): FixtureCaseState {
    const state = database.cases[caseId];
    if (!state) throw problem("CASE_NOT_FOUND", 404, "Fixture 病例不存在。", instance);
    return state;
  }

  async createCase(payload: CaseCreateRequest): Promise<CaseResponse> {
    this.authenticate("/api/v2/cases");
    const database = this.read();
    database.sequence += 1;
    const caseId = `fixture_case_${database.sequence}`;
    const now = isoNow();
    const caseResource: CaseResponse = {
      id: caseId,
      customer_name: payload.customer_name,
      consultant_id: payload.consultant_id,
      status: "intake",
      notes: payload.notes,
      clinical_summary: null,
      created_at: now,
      updated_at: now,
      attachments: []
    };
    database.cases[caseId] = {
      caseResource,
      analysis: null,
      draft: null,
      report: null,
      operation: null,
      draftRetried: false
    };
    this.write(database);
    return structuredClone(caseResource);
  }

  async getCase(caseId: string): Promise<CaseResponse> {
    const instance = `/api/v2/cases/${caseId}`;
    this.authenticate(instance);
    return structuredClone(this.caseState(this.read(), caseId, instance).caseResource);
  }

  async updateClinicalSummary(caseId: string, payload: ClinicalSummaryUpdateRequest): Promise<CaseResponse> {
    const instance = `/api/v2/cases/${caseId}/clinical-summary`;
    this.authenticate(instance);
    const database = this.read();
    const state = this.caseState(database, caseId, instance);
    state.caseResource.clinical_summary = payload.clinical_summary;
    state.caseResource.updated_at = isoNow();
    this.write(database);
    return structuredClone(state.caseResource);
  }

  async uploadAttachments(caseId: string, attachmentType: AttachmentType, files: File[]): Promise<AttachmentBatchResponse> {
    const instance = `/api/v2/cases/${caseId}/attachments`;
    this.authenticate(instance);
    const database = this.read();
    const state = this.caseState(database, caseId, instance);
    const items = files.map((file, index) => {
      const fail = this.scenario === "attachment_partial_failure" && index === files.length - 1;
      const duplicate = state.caseResource.attachments.some((item) => item.filename === file.name);
      if (fail) {
        return {
          file_id: null,
          filename: file.name,
          attachment_type: attachmentType,
          status: "failed" as const,
          media_type: file.type || "application/octet-stream",
          size_bytes: file.size,
          parse_status: null,
          lab_item_count: 0,
          warnings: [],
          failure: { code: "FIXTURE_FILE_REJECTED", message: "Fixture 模拟：单文件处理失败。", retryable: false }
        };
      }
      const fileId = attachmentType === "medical_record" ? "file_fixture_medical" : "file_fixture_questionnaire";
      if (attachmentType === "medical_record" && !duplicate) {
        const attachment: AttachmentResponse = {
          id: fileId,
          filename: file.name,
          attachment_type: "medical_record",
          media_type: file.type || "text/plain",
          size_bytes: file.size,
          uploaded_at: isoNow(),
          intake_status: "uploaded",
          parse_status: "parsed",
          parse_confidence: 0.99,
          needs_manual_review: false,
          missing_fields: [],
          page_count: 1,
          is_scanned: false,
          warning: null,
          error: null
        };
        state.caseResource.attachments.push(attachment);
      }
      return {
        file_id: fileId,
        filename: file.name,
        attachment_type: attachmentType,
        status: duplicate ? "duplicate" as const : attachmentType === "questionnaire" ? "questionnaire_imported" as const : "parsed" as const,
        media_type: file.type || "text/plain",
        size_bytes: file.size,
        parse_status: attachmentType === "medical_record" ? "parsed" as const : null,
        lab_item_count: attachmentType === "medical_record" ? 1 : 0,
        warnings: [],
        failure: null
      };
    });
    const acceptedCount = items.filter((item) => item.status !== "failed").length;
    state.caseResource.status = acceptedCount ? "parsing_completed" : state.caseResource.status;
    state.caseResource.updated_at = isoNow();
    this.write(database);
    return {
      items,
      meta: {
        case_id: caseId,
        case_status: state.caseResource.status,
        accepted_count: acceptedCount,
        failed_count: items.length - acceptedCount
      }
    };
  }

  async startAnalysis(caseId: string, payload: StartAnalysisRequest): Promise<AcceptedOperation> {
    const instance = `/api/v2/cases/${caseId}/analyses`;
    this.authenticate(instance);
    const database = this.read();
    const state = this.caseState(database, caseId, instance);
    if (!payload.third_party_processing_confirmed) {
      throw problem("REQUEST_VALIDATION_FAILED", 422, "Fixture 模拟：必须确认第三方处理。", instance);
    }
    const analysisId = `fixture_analysis_${caseId}`;
    state.analysis = createFixtureAnalysis(caseId, analysisId);
    state.operation = {
      polls: 0,
      response: {
        operation_id: analysisId,
        kind: "case_workflow",
        stage: "analysis",
        status: "queued",
        case_id: caseId,
        analysis_id: analysisId,
        draft_id: null,
        progress: { current: 0, total: 2, percent: 0, current_item: null },
        failure: null,
        created_at: isoNow(),
        updated_at: isoNow()
      }
    };
    this.write(database);
    return { operation: structuredClone(state.operation.response), location: `/api/v2/operations/${analysisId}` };
  }

  async getOperation(operationId: string): Promise<OperationResponse> {
    const instance = `/api/v2/operations/${operationId}`;
    this.authenticate(instance);
    const database = this.read();
    const state = Object.values(database.cases).find((item) => item.operation?.response.operation_id === operationId);
    if (!state?.operation || !state.analysis) throw problem("ANALYSIS_NOT_FOUND", 404, "Fixture Operation 不存在。", instance);
    state.operation.polls += 1;
    const operation = state.operation.response;
    operation.status = "running";
    operation.progress = { current: 1, total: 2, percent: 50, current_item: "fixture-medical-record.txt" };
    operation.updated_at = isoNow();

    if (state.operation.polls >= 2) {
      if (operation.stage === "analysis" && this.scenario === "analysis_failure") {
        operation.status = "failed";
        operation.failure = { code: "ANALYSIS_FAILED", message: "Fixture 模拟：综合分析失败。", retryable: true };
        state.analysis.status = "failed";
        state.analysis.error = operation.failure;
      } else if (operation.stage === "draft_generation" && this.scenario === "draft_generation_failure" && !state.draftRetried) {
        operation.status = "failed";
        operation.failure = { code: "DRAFT_GENERATION_FAILED", message: "Fixture 模拟：草案生成失败。", retryable: true };
        state.analysis.draft_generation = { status: "failed", progress: 100, error: operation.failure.message };
      } else {
        operation.status = "succeeded";
        operation.progress = { current: 2, total: 2, percent: 100, current_item: null };
        if (operation.stage === "analysis") {
          state.analysis.status = "ready_for_review";
          state.analysis.progress = { current: 2, total: 2, percent: 100, current_file_name: null };
        } else {
          const draftId = `fixture_draft_${state.caseResource.id}`;
          state.draft = createFixtureDraft(state.caseResource.id, draftId);
          state.analysis.status = "reviewed";
          state.analysis.draft_id = draftId;
          state.analysis.draft_generation = { status: "ready", progress: 100, error: null };
          state.caseResource.status = "draft_generated";
          operation.draft_id = draftId;
        }
      }
    }
    state.analysis.updated_at = isoNow();
    this.write(database);
    return structuredClone(operation);
  }

  async getLatestAnalysis(caseId: string): Promise<AnalysisResponse> {
    const instance = `/api/v2/cases/${caseId}/analyses/latest`;
    this.authenticate(instance);
    const state = this.caseState(this.read(), caseId, instance);
    if (!state.analysis) throw problem("ANALYSIS_NOT_FOUND", 404, "Fixture 分析不存在。", instance);
    return structuredClone(state.analysis);
  }

  async submitReview(caseId: string, analysisId: string, payload: ReviewSubmitRequest): Promise<AcceptedOperation> {
    const instance = `/api/v2/cases/${caseId}/analyses/${analysisId}/reviews`;
    this.authenticate(instance);
    const database = this.read();
    const state = this.caseState(database, caseId, instance);
    if (!state.analysis || state.analysis.id !== analysisId) throw problem("ANALYSIS_NOT_FOUND", 404, "Fixture 分析不存在。", instance);
    if (this.scenario === "revision_conflict" || payload.expected_revision !== state.analysis.revision) {
      throw problem("ANALYSIS_REVISION_CONFLICT", 409, "Fixture 模拟：分析修订号冲突。", instance);
    }
    state.analysis.revision += 1;
    state.analysis.status = "reviewed";
    state.analysis.draft_generation = { status: "queued", progress: 0, error: null };
    state.operation = {
      polls: 0,
      response: {
        operation_id: analysisId,
        kind: "case_workflow",
        stage: "draft_generation",
        status: "queued",
        case_id: caseId,
        analysis_id: analysisId,
        draft_id: null,
        progress: { current: 0, total: 2, percent: 0, current_item: null },
        failure: null,
        created_at: isoNow(),
        updated_at: isoNow()
      }
    };
    this.write(database);
    return { operation: structuredClone(state.operation.response), location: `/api/v2/operations/${analysisId}` };
  }

  async retryDraftGeneration(caseId: string, analysisId: string): Promise<AcceptedOperation> {
    const instance = `/api/v2/cases/${caseId}/analyses/${analysisId}/draft-generation:retry`;
    this.authenticate(instance);
    const database = this.read();
    const state = this.caseState(database, caseId, instance);
    if (!state.analysis || state.analysis.id !== analysisId) throw problem("ANALYSIS_NOT_FOUND", 404, "Fixture 分析不存在。", instance);
    state.draftRetried = true;
    state.analysis.draft_generation = { status: "queued", progress: 0, error: null };
    state.operation = {
      polls: 0,
      response: {
        operation_id: analysisId,
        kind: "case_workflow",
        stage: "draft_generation",
        status: "queued",
        case_id: caseId,
        analysis_id: analysisId,
        draft_id: null,
        progress: { current: 0, total: 2, percent: 0, current_item: null },
        failure: null,
        created_at: isoNow(),
        updated_at: isoNow()
      }
    };
    this.write(database);
    return { operation: structuredClone(state.operation.response), location: `/api/v2/operations/${analysisId}` };
  }

  async getDraft(draftId: string): Promise<DraftResponse> {
    const instance = `/api/v2/drafts/${draftId}`;
    this.authenticate(instance);
    const state = Object.values(this.read().cases).find((item) => item.draft?.id === draftId);
    if (!state?.draft) throw problem("DRAFT_NOT_FOUND", 404, "Fixture 草案不存在。", instance);
    return structuredClone(state.draft);
  }

  async approveDraft(draftId: string, payload: ApprovalRequest): Promise<ApprovalResponse> {
    const instance = `/api/v2/drafts/${draftId}/approval`;
    this.authenticate(instance);
    if (this.scenario === "approval_validation_error") {
      throw problem("REQUEST_VALIDATION_FAILED", 422, "Fixture 模拟：审批请求校验失败。", instance);
    }
    const database = this.read();
    const state = Object.values(database.cases).find((item) => item.draft?.id === draftId);
    if (!state?.draft) throw problem("DRAFT_NOT_FOUND", 404, "Fixture 草案不存在。", instance);
    state.draft.status = "approved";
    state.caseResource.status = "approved";
    const approvedAt = isoNow();
    if (this.scenario !== "report_not_ready") {
      state.report = {
        draft_id: draftId,
        status: "ready",
        filename: `fixture-report-${draftId}.pdf`,
        download_url: `/api/v2/drafts/${draftId}/report.pdf`,
        approved_at: approvedAt
      };
    }
    this.write(database);
    return {
      draft_id: draftId,
      status: "approved",
      reviewer_id: payload.reviewer_id,
      publishable_report: payload.publishable_summary ?? state.draft.public_summary.join("\n\n"),
      approved_at: approvedAt,
      report_ready: Boolean(state.report),
      report_url: `/api/v2/drafts/${draftId}/report.pdf`
    };
  }

  async getReport(draftId: string): Promise<ReportResponse> {
    const instance = `/api/v2/drafts/${draftId}/report`;
    this.authenticate(instance);
    const state = Object.values(this.read().cases).find((item) => item.draft?.id === draftId);
    if (!state?.draft) throw problem("DRAFT_NOT_FOUND", 404, "Fixture 草案不存在。", instance);
    if (!state.report) throw problem("REPORT_NOT_READY", 409, "Fixture 模拟：报告尚未就绪。", instance);
    return structuredClone(state.report);
  }

  async downloadReport(draftId: string): Promise<DownloadedReport> {
    const report = await this.getReport(draftId);
    return {
      blob: new Blob(["%PDF-1.4\n% Fixture-only synthetic report\n%%EOF\n"], { type: "application/pdf" }),
      filename: report.filename
    };
  }
}
