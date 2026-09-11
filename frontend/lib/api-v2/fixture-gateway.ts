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
  CaseListResponse,
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
  medicalRecordHashes?: Record<string, string>;
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
    evidence_status: "verified_text",
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
    evidence_status: "verified_text"
  };
  return {
    id: analysisId,
    case_id: caseId,
    version: 1,
    revision: 1,
    status: "queued",
    progress: { current: 0, total: 2, percent: 5, current_file_name: null },
    case_summary: "完全虚构的病例摘要，用于验证前端工作流，不代表医学建议。",
    system_findings: ["虚构系统发现：需要医生复核维生素 D 相关信息。"],
    abnormal_findings: [finding],
    current_supplements: [supplement],
    food_sensitivity: {
      source_file_id: "file_fixture_questionnaire",
      source_file_name: "fixture-questionnaire.txt",
      source_page: 1,
      items: [
        food,
        {
          id: "food_fixture_corn",
          name: "玉米",
          raw_value: "83.9",
          unit: "U/mL",
          abnormal_flag: "positive",
          severity: "mild",
          reported_grade: "1",
          reported_grade_meaning: "虚构轻度反应",
          reference_range: "50-100 U/mL",
          grading_basis: "Fixture 分级",
          source_page: 1,
          source_text: "Corn: 83.9 U/mL",
          evidence_status: "verified_text"
        },
        {
          id: "food_fixture_yeast",
          name: "酵母",
          raw_value: ">200",
          unit: "U/mL",
          abnormal_flag: "high",
          severity: "high",
          reported_grade: "3",
          reported_grade_meaning: "虚构重度反应",
          reference_range: ">200 U/mL",
          grading_basis: "Fixture 分级",
          source_page: 1,
          source_text: "Yeast: >200 U/mL",
          evidence_status: "verified_text"
        }
      ],
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
    core_health_portrait: null,
    structured_system_findings: [],
    lifestyle_plan: null,
    safety_decisions: [],
    uncovered_system_ids: [],
    uncovered_system_reasons: {},
    report_sections: [],
    generated_at: isoNow()
  };
}

function invalidFixtureRequest(instance: string, detail: string, status = 422): never {
  throw problem("REQUEST_VALIDATION_FAILED", status, `Fixture 模拟：${detail}`, instance);
}

function applyReviewDeltas(
  analysis: AnalysisResponse,
  payload: ReviewSubmitRequest,
  instance: string
): void {
  const findingIds = new Set<string>();
  let findingSequence = 0;
  for (const change of payload.finding_changes) {
    if (change.op === "add") {
      findingSequence += 1;
      analysis.abnormal_findings.push({
        id: `finding_fixture_added_${analysis.revision}_${findingSequence}`,
        ...change.value,
        interpretation: null,
        report_explanation: null,
        neutral_interpretation: null,
        support_need_text: null,
        confidence: 1,
        evidence_status: "doctor_added",
        evidence_notes: [],
        observed_at: null
      });
      continue;
    }
    if (findingIds.has(change.id)) invalidFixtureRequest(instance, `同一指标 ${change.id} 不能重复操作。`);
    findingIds.add(change.id);
    const index = analysis.abnormal_findings.findIndex((item) => item.id === change.id);
    if (index < 0) invalidFixtureRequest(instance, `指标 ${change.id} 不存在。`);
    if (change.op === "remove") analysis.abnormal_findings.splice(index, 1);
    else analysis.abnormal_findings[index] = { ...analysis.abnormal_findings[index], ...change.changes };
  }

  const supplementIds = new Set<string>();
  let supplementSequence = 0;
  for (const change of payload.supplement_changes) {
    if (change.op === "add") {
      supplementSequence += 1;
      analysis.current_supplements.push({
        id: `supplement_fixture_added_${analysis.revision}_${supplementSequence}`,
        name: change.value.name,
        source_file_ids: [],
        source_file_names: [],
        doctor_added: true
      });
      continue;
    }
    if (supplementIds.has(change.id)) invalidFixtureRequest(instance, `同一补充剂 ${change.id} 不能重复操作。`);
    supplementIds.add(change.id);
    const index = analysis.current_supplements.findIndex((item) => item.id === change.id);
    if (index < 0) invalidFixtureRequest(instance, `补充剂 ${change.id} 不存在。`);
    if (change.op === "remove") analysis.current_supplements.splice(index, 1);
    else analysis.current_supplements[index] = { ...analysis.current_supplements[index], ...change.changes };
  }

  const foodIds = new Set<string>();
  let foodSequence = 0;
  for (const change of payload.food_sensitivity_changes) {
    if (change.op === "add") {
      foodSequence += 1;
      const { source_file_id, source_file_name, ...value } = change.value;
      if (!analysis.food_sensitivity) {
        analysis.food_sensitivity = {
          source_file_id,
          source_file_name,
          source_page: value.source_page,
          items: [],
          interpretations: [],
          valid: true,
          warning: null
        };
      }
      analysis.food_sensitivity.items.push({
        id: `food_fixture_added_${analysis.revision}_${foodSequence}`,
        ...value,
        evidence_status: "doctor_added"
      });
      continue;
    }
    if (foodIds.has(change.id)) invalidFixtureRequest(instance, `同一食敏条目 ${change.id} 不能重复操作。`);
    foodIds.add(change.id);
    const index = analysis.food_sensitivity?.items.findIndex((item) => item.id === change.id) ?? -1;
    if (index < 0 || !analysis.food_sensitivity) invalidFixtureRequest(instance, `食敏条目 ${change.id} 不存在。`);
    if (change.op === "remove") analysis.food_sensitivity.items.splice(index, 1);
    else analysis.food_sensitivity.items[index] = { ...analysis.food_sensitivity.items[index], ...change.changes };
  }
}

function applyApprovalEdits(draft: DraftResponse, payload: ApprovalRequest, instance: string): DraftResponse {
  const excludedIds = new Set(payload.excluded_sku_ids);
  const overrides = new Map(payload.dosage_overrides.map((item) => [item.sku_id, item]));
  const knownIds = new Set(draft.recommended_skus.map((item) => item.sku_id));
  const unknownIds = [...excludedIds, ...overrides.keys()].filter((skuId) => !knownIds.has(skuId));
  if (unknownIds.length) invalidFixtureRequest(instance, `审批引用了未知 SKU：${unknownIds.join("、")}。`);
  if ([...excludedIds].some((skuId) => overrides.has(skuId))) {
    invalidFixtureRequest(instance, "同一 SKU 不能同时排除和改选剂量。");
  }

  const recommendations = draft.recommended_skus
    .filter((item) => !excludedIds.has(item.sku_id))
    .map((item) => {
      const override = overrides.get(item.sku_id);
      if (!override) return item;
      const option = item.dosage_options.find((candidate) => candidate.option_id === override.option_id);
      if (!option) invalidFixtureRequest(instance, `${item.display_name} 的剂量选项无效。`);
      if (option.option_id !== item.dosage_option_id && !(override.note ?? "").trim()) {
        invalidFixtureRequest(instance, `${item.display_name} 改选非默认剂量时必须填写说明。`);
      }
      return {
        ...item,
        dosage: option.display_text,
        dosage_option_id: option.option_id,
        dosage_option_label: option.label,
        dosage_regimen: option.regimen,
        dosage_match_reasons: option.option_id === item.dosage_option_id
          ? item.dosage_match_reasons
          : [...item.dosage_match_reasons, `医生人工改档：${option.label}；备注：${override.note?.trim()}`]
      };
    });
  if (!recommendations.length) invalidFixtureRequest(instance, "至少保留一项营养素推荐后才能审核发布。", 409);

  return {
    ...draft,
    status: "approved",
    public_summary: payload.publishable_summary === null ? draft.public_summary : [payload.publishable_summary],
    recommended_skus: recommendations
  };
}

async function fileSha256(file: File): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
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

  async listCases(offset = 0, limit = 50): Promise<CaseListResponse> {
    this.authenticate("/api/v2/cases");
    const items = Object.values(this.read().cases)
      .map((state) => state.caseResource)
      .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
      .map((item) => ({
        id: item.id,
        customer_name: item.customer_name,
        consultant_id: item.consultant_id,
        status: item.status,
        attachment_count: item.attachments.length,
        created_at: item.created_at,
        updated_at: item.updated_at
      }));
    return { items: items.slice(offset, offset + limit), total: items.length, offset, limit };
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
      draftRetried: false,
      medicalRecordHashes: {}
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
    const items: AttachmentBatchResponse["items"] = [];
    const medicalRecordHashes = state.medicalRecordHashes ??= {};
    for (const [index, file] of files.entries()) {
      const fail = this.scenario === "attachment_partial_failure" && index === files.length - 1;
      if (fail) {
        items.push({
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
        });
        continue;
      }
      const contentHash = attachmentType === "medical_record" ? await fileSha256(file) : null;
      const duplicateId = contentHash
        ? Object.entries(medicalRecordHashes).find(([, hash]) => hash === contentHash)?.[0] ?? null
        : null;
      const fileId = duplicateId
        ?? (attachmentType === "medical_record"
          ? state.caseResource.attachments.length === 0
            ? "file_fixture_medical"
            : `file_fixture_medical_${state.caseResource.attachments.length + 1}`
          : "file_fixture_questionnaire");
      if (attachmentType === "medical_record" && !duplicateId) {
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
        medicalRecordHashes[fileId] = contentHash!;
      }
      items.push({
        file_id: fileId,
        filename: file.name,
        attachment_type: attachmentType,
        status: duplicateId ? "duplicate" : attachmentType === "questionnaire" ? "questionnaire_imported" : "parsed",
        media_type: file.type || "text/plain",
        size_bytes: file.size,
        parse_status: attachmentType === "medical_record" ? "parsed" as const : null,
        lab_item_count: attachmentType === "medical_record" ? 1 : 0,
        warnings: [],
        failure: null
      });
    }
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
        progress: { current: 0, total: 2, percent: 5, current_item: "排队中" },
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
    if (operation.stage === "analysis") {
      operation.progress = { current: 1, total: 2, percent: 47, current_item: "文件分析 1/2 处理中：fixture-medical-record.txt" };
      state.analysis.status = "analyzing_documents";
      state.analysis.progress = { current: 1, total: 2, percent: 47, current_file_name: "fixture-medical-record.txt" };
    } else {
      operation.progress = { current: 50, total: 100, percent: 50, current_item: "最终病例深度综合" };
      state.analysis.draft_generation = { status: "final_synthesizing", progress: 50, error: null };
    }
    operation.updated_at = isoNow();

    if (state.operation.polls >= 2) {
      if (operation.stage === "analysis" && this.scenario === "analysis_failure") {
        operation.status = "failed";
        operation.failure = { code: "ANALYSIS_FAILED", message: "Fixture 模拟：综合分析失败。", retryable: true };
        operation.progress = { current: 2, total: 2, percent: 100, current_item: "综合分析失败" };
        state.analysis.status = "failed";
        state.analysis.progress = { current: 2, total: 2, percent: 100, current_file_name: null };
        state.analysis.error = operation.failure;
      } else if (operation.stage === "draft_generation" && this.scenario === "draft_generation_failure" && !state.draftRetried) {
        operation.status = "failed";
        operation.failure = { code: "DRAFT_GENERATION_FAILED", message: "Fixture 模拟：草案生成失败。", retryable: true };
        operation.progress = { current: 50, total: 100, percent: 50, current_item: "草案生成失败" };
        state.analysis.draft_generation = { status: "failed", progress: 50, error: operation.failure.message };
      } else {
        operation.status = "succeeded";
        operation.progress = operation.stage === "analysis"
          ? { current: 2, total: 2, percent: 100, current_item: "综合分析已完成，可以开始医生校对" }
          : { current: 100, total: 100, percent: 100, current_item: "草案生成完成" };
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
    applyReviewDeltas(state.analysis, payload, instance);
    state.analysis.revision += 1;
    state.analysis.status = "reviewed";
    state.analysis.draft_generation = { status: "queued", progress: 5, error: null };
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
        progress: { current: 5, total: 100, percent: 5, current_item: "医生校对已保存，任务排队中" },
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
    state.analysis.draft_generation = { status: "queued", progress: 5, error: null };
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
        progress: { current: 5, total: 100, percent: 5, current_item: "医生校对已保存，任务排队中" },
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
    if (payload.expected_revision !== state.draft.revision) {
      throw problem("DRAFT_REVISION_CONFLICT", 409, "Fixture draft revision changed.", instance);
    }
    state.draft = applyApprovalEdits(state.draft, payload, instance);
    state.caseResource.status = "approved";
    const approvedAt = isoNow();
    if (this.scenario !== "report_not_ready") {
      state.report = {
        draft_id: draftId,
        status: "ready",
        filename: `fixture-report-${draftId}.pdf`,
        download_url: `/api/v2/drafts/${draftId}/report.pdf`,
        reviewer_id: "fixture-doctor",
        publishable_report: payload.publishable_summary ?? state.draft.public_summary.join("\n\n"),
        approved_at: approvedAt
      };
    }
    this.write(database);
    return {
      draft_id: draftId,
      status: "approved",
      reviewer_id: "fixture-doctor",
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
