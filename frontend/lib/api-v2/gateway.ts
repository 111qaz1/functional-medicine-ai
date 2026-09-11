import type {
  AnalysisResponse,
  ApprovalRequest,
  ApprovalResponse,
  AttachmentBatchResponse,
  AttachmentType,
  CaseCreateRequest,
  CaseListResponse,
  CaseResponse,
  ClinicalSummaryUpdateRequest,
  DraftResponse,
  OperationResponse,
  ProblemDetails,
  ReportResponse,
  ReviewSubmitRequest,
  StartAnalysisRequest
} from "./types";

export interface AcceptedOperation {
  operation: OperationResponse;
  location: string | null;
}

export interface DownloadedReport {
  blob: Blob;
  filename: string;
}

export interface WorkflowGateway {
  listCases(offset?: number, limit?: number): Promise<CaseListResponse>;
  createCase(payload: CaseCreateRequest): Promise<CaseResponse>;
  getCase(caseId: string): Promise<CaseResponse>;
  updateClinicalSummary(caseId: string, payload: ClinicalSummaryUpdateRequest): Promise<CaseResponse>;
  uploadAttachments(caseId: string, attachmentType: AttachmentType, files: File[]): Promise<AttachmentBatchResponse>;
  startAnalysis(caseId: string, payload: StartAnalysisRequest): Promise<AcceptedOperation>;
  getOperation(operationId: string, signal?: AbortSignal): Promise<OperationResponse>;
  getLatestAnalysis(caseId: string): Promise<AnalysisResponse>;
  submitReview(caseId: string, analysisId: string, payload: ReviewSubmitRequest): Promise<AcceptedOperation>;
  retryDraftGeneration(caseId: string, analysisId: string): Promise<AcceptedOperation>;
  getDraft(draftId: string): Promise<DraftResponse>;
  approveDraft(draftId: string, payload: ApprovalRequest): Promise<ApprovalResponse>;
  getReport(draftId: string): Promise<ReportResponse>;
  downloadReport(draftId: string): Promise<DownloadedReport>;
}

export class WorkflowProblem extends Error {
  readonly problem: ProblemDetails;

  constructor(problem: ProblemDetails) {
    super(problem.detail || problem.title);
    this.name = "WorkflowProblem";
    this.problem = problem;
  }
}

function isProblemDetails(value: unknown): value is ProblemDetails {
  if (!value || typeof value !== "object") return false;
  const problem = value as Record<string, unknown>;
  return (
    typeof problem.type === "string" &&
    typeof problem.title === "string" &&
    typeof problem.status === "number" &&
    typeof problem.detail === "string" &&
    typeof problem.instance === "string" &&
    typeof problem.code === "string" &&
    Array.isArray(problem.errors)
  );
}

function fallbackProblem(response: Response): ProblemDetails {
  return {
    type: "urn:fm-ai:problem:gateway-response-invalid",
    title: "Gateway response invalid",
    status: response.status,
    detail: "服务端返回了无法识别的错误响应，请联系系统管理员。",
    instance: "",
    code: "GATEWAY_RESPONSE_INVALID",
    errors: []
  };
}

function invalidSuccessProblem(instance: string): WorkflowProblem {
  return new WorkflowProblem({
    type: "urn:fm-ai:problem:gateway-response-invalid",
    title: "Gateway response invalid",
    status: 502,
    detail: "服务端返回了无法识别的成功响应，请联系系统管理员。",
    instance,
    code: "GATEWAY_RESPONSE_INVALID",
    errors: []
  });
}

type ResponseShapeRule =
  | "string"
  | "number"
  | "boolean"
  | "array"
  | "object"
  | "nullable-string"
  | "nullable-object"
  | EnumRule
  | ResponseShape;

interface EnumRule {
  oneOf: readonly string[];
}

interface ResponseShape {
  [key: string]: ResponseShapeRule;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function isEnumRule(rule: EnumRule | ResponseShape): rule is EnumRule {
  return Array.isArray((rule as EnumRule).oneOf);
}

function matchesShape(value: unknown, shape: ResponseShape): boolean {
  if (!isRecord(value)) return false;
  return Object.entries(shape).every(([key, rule]) => {
    if (!(key in value)) return false;
    const field = value[key];
    if (typeof rule === "string") {
      if (rule === "array") return Array.isArray(field);
      if (rule === "object") return isRecord(field);
      if (rule === "nullable-string") return field === null || typeof field === "string";
      if (rule === "nullable-object") return field === null || isRecord(field);
      return typeof field === rule;
    }
    if (isEnumRule(rule)) return typeof field === "string" && rule.oneOf.includes(field);
    return matchesShape(field, rule);
  });
}

const CASE_RESPONSE_SHAPE: ResponseShape = {
  id: "string",
  customer_name: "string",
  consultant_id: "nullable-string",
  status: { oneOf: ["intake", "files_received", "parsing_completed", "ready_for_recommendation", "draft_generated", "under_review", "approved"] },
  notes: "nullable-string",
  clinical_summary: "nullable-string",
  created_at: "string",
  updated_at: "string",
  attachments: "array"
};

const CASE_LIST_RESPONSE_SHAPE: ResponseShape = {
  items: "array",
  total: "number",
  offset: "number",
  limit: "number"
};

const ATTACHMENT_BATCH_SHAPE: ResponseShape = {
  items: "array",
  meta: {
    case_id: "string",
    case_status: { oneOf: ["intake", "files_received", "parsing_completed", "ready_for_recommendation", "draft_generated", "under_review", "approved"] },
    accepted_count: "number",
    failed_count: "number"
  }
};

const OPERATION_RESPONSE_SHAPE: ResponseShape = {
  operation_id: "string",
  kind: { oneOf: ["case_workflow"] },
  stage: { oneOf: ["analysis", "draft_generation"] },
  status: { oneOf: ["queued", "running", "succeeded", "failed"] },
  case_id: "string",
  analysis_id: "string",
  draft_id: "nullable-string",
  progress: { current: "number", total: "number", percent: "number", current_item: "nullable-string" },
  failure: "nullable-object",
  created_at: "string",
  updated_at: "string"
};

const ANALYSIS_RESPONSE_SHAPE: ResponseShape = {
  id: "string",
  case_id: "string",
  version: "number",
  revision: "number",
  status: { oneOf: ["queued", "preparing", "analyzing_documents", "synthesizing", "validating", "ready_for_review", "reviewed", "stale", "failed"] },
  progress: { current: "number", total: "number", percent: "number", current_file_name: "nullable-string" },
  case_summary: "nullable-string",
  system_findings: "array",
  abnormal_findings: "array",
  current_supplements: "array",
  food_sensitivity: "nullable-object",
  warnings: "array",
  error: "nullable-object",
  draft_generation: {
    status: { oneOf: ["idle", "queued", "final_synthesizing", "validating_support_needs", "mapping_products", "checking_safety", "generating_draft", "ready", "failed"] },
    progress: "number",
    error: "nullable-string"
  },
  draft_id: "nullable-string",
  created_at: "string",
  updated_at: "string"
};

const DRAFT_RESPONSE_SHAPE: ResponseShape = {
  id: "string",
  case_id: "string",
  status: { oneOf: ["pending_review", "approved", "abstained"] },
  revision: "number",
  public_summary: "array",
  key_lab_highlights: "array",
  recommended_skus: "array",
  lifestyle_actions: "array",
  rationale: "array",
  evidence_details: "array",
  contraindications: "array",
  missing_info: "array",
  confidence: "number",
  abstain_reason: "nullable-string",
  manual_review_required: "boolean",
  red_flags: "array",
  core_health_portrait: "nullable-object",
  structured_system_findings: "array",
  lifestyle_plan: "nullable-object",
  safety_decisions: "array",
  uncovered_system_ids: "array",
  uncovered_system_reasons: "object",
  report_sections: "array",
  generated_at: "string"
};

const APPROVAL_RESPONSE_SHAPE: ResponseShape = {
  draft_id: "string",
  status: { oneOf: ["approved"] },
  reviewer_id: "string",
  publishable_report: "string",
  approved_at: "string",
  report_ready: "boolean",
  report_url: "string"
};

const REPORT_RESPONSE_SHAPE: ResponseShape = {
  draft_id: "string",
  status: { oneOf: ["ready"] },
  filename: "string",
  download_url: "string",
  reviewer_id: "string",
  publishable_report: "string",
  approved_at: "string"
};

async function throwProblem(response: Response): Promise<never> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new WorkflowProblem(fallbackProblem(response));
  }
  throw new WorkflowProblem(isProblemDetails(payload) ? payload : fallbackProblem(response));
}

function apiPath(...parts: string[]): string {
  return `/api/v2/${parts.map((part) => encodeURIComponent(part)).join("/")}`;
}

function parseFilename(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded.replace(/^"|"$/g, ""));
    } catch {
      return fallback;
    }
  }
  return disposition.match(/filename="([^"]+)"/i)?.[1] ?? fallback;
}

function notifyExpiredSession(response: Response): void {
  if (response.status === 401 && typeof window !== "undefined") {
    window.dispatchEvent(new Event("fm-session-expired"));
  }
}

export class HttpWorkflowGateway implements WorkflowGateway {
  private readonly fetchImpl: typeof fetch;

  constructor(fetchImpl: typeof fetch = globalThis.fetch) {
    // Browser-native fetch requires the global object as its receiver. Keeping an
    // unbound fetch as a class property makes `this.fetchImpl(...)` use the
    // gateway instance as the receiver and Chromium rejects it as an illegal
    // invocation before the request is sent.
    this.fetchImpl = fetchImpl.bind(globalThis);
  }

  private async json<T>(url: string, shape: ResponseShape, init?: RequestInit): Promise<{ data: T; response: Response }> {
    const response = await this.fetchImpl(url, {
      cache: "no-store",
      credentials: "include",
      ...init,
      headers: {
        Accept: "application/json, application/problem+json",
        ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
        ...init?.headers
      }
    });
    if (!response.ok) {
      notifyExpiredSession(response);
      await throwProblem(response);
    }
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw invalidSuccessProblem(url);
    }
    if (!matchesShape(payload, shape)) throw invalidSuccessProblem(url);
    return { data: payload as T, response };
  }

  async listCases(offset = 0, limit = 50): Promise<CaseListResponse> {
    return (
      await this.json<CaseListResponse>(
        `${apiPath("cases")}?offset=${offset}&limit=${limit}`,
        CASE_LIST_RESPONSE_SHAPE
      )
    ).data;
  }

  async createCase(payload: CaseCreateRequest): Promise<CaseResponse> {
    return (await this.json<CaseResponse>(apiPath("cases"), CASE_RESPONSE_SHAPE, { method: "POST", body: JSON.stringify(payload) })).data;
  }

  async getCase(caseId: string): Promise<CaseResponse> {
    return (await this.json<CaseResponse>(apiPath("cases", caseId), CASE_RESPONSE_SHAPE)).data;
  }

  async updateClinicalSummary(caseId: string, payload: ClinicalSummaryUpdateRequest): Promise<CaseResponse> {
    return (await this.json<CaseResponse>(apiPath("cases", caseId, "clinical-summary"), CASE_RESPONSE_SHAPE, {
      method: "PUT",
      body: JSON.stringify(payload)
    })).data;
  }

  async uploadAttachments(caseId: string, attachmentType: AttachmentType, files: File[]): Promise<AttachmentBatchResponse> {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    form.append("attachment_type", attachmentType);
    return (await this.json<AttachmentBatchResponse>(apiPath("cases", caseId, "attachments"), ATTACHMENT_BATCH_SHAPE, {
      method: "POST",
      body: form
    })).data;
  }

  async startAnalysis(caseId: string, payload: StartAnalysisRequest): Promise<AcceptedOperation> {
    const { data, response } = await this.json<OperationResponse>(apiPath("cases", caseId, "analyses"), OPERATION_RESPONSE_SHAPE, {
      method: "POST",
      body: JSON.stringify(payload)
    });
    return { operation: data, location: response.headers.get("Location") };
  }

  async getOperation(operationId: string, signal?: AbortSignal): Promise<OperationResponse> {
    return (await this.json<OperationResponse>(apiPath("operations", operationId), OPERATION_RESPONSE_SHAPE, { signal })).data;
  }

  async getLatestAnalysis(caseId: string): Promise<AnalysisResponse> {
    return (await this.json<AnalysisResponse>(apiPath("cases", caseId, "analyses", "latest"), ANALYSIS_RESPONSE_SHAPE)).data;
  }

  async submitReview(caseId: string, analysisId: string, payload: ReviewSubmitRequest): Promise<AcceptedOperation> {
    const { data, response } = await this.json<OperationResponse>(
      apiPath("cases", caseId, "analyses", analysisId, "reviews"),
      OPERATION_RESPONSE_SHAPE,
      { method: "POST", body: JSON.stringify(payload) }
    );
    return { operation: data, location: response.headers.get("Location") };
  }

  async retryDraftGeneration(caseId: string, analysisId: string): Promise<AcceptedOperation> {
    const { data, response } = await this.json<OperationResponse>(
      `${apiPath("cases", caseId, "analyses", analysisId, "draft-generation")}:retry`,
      OPERATION_RESPONSE_SHAPE,
      { method: "POST" }
    );
    return { operation: data, location: response.headers.get("Location") };
  }

  async getDraft(draftId: string): Promise<DraftResponse> {
    return (await this.json<DraftResponse>(apiPath("drafts", draftId), DRAFT_RESPONSE_SHAPE)).data;
  }

  async approveDraft(draftId: string, payload: ApprovalRequest): Promise<ApprovalResponse> {
    return (await this.json<ApprovalResponse>(apiPath("drafts", draftId, "approval"), APPROVAL_RESPONSE_SHAPE, {
      method: "POST",
      body: JSON.stringify(payload)
    })).data;
  }

  async getReport(draftId: string): Promise<ReportResponse> {
    return (await this.json<ReportResponse>(apiPath("drafts", draftId, "report"), REPORT_RESPONSE_SHAPE)).data;
  }

  async downloadReport(draftId: string): Promise<DownloadedReport> {
    const response = await this.fetchImpl(`${apiPath("drafts", draftId, "report")}.pdf`, {
      cache: "no-store",
      credentials: "include",
      headers: { Accept: "application/pdf, application/problem+json" }
    });
    if (!response.ok) {
      notifyExpiredSession(response);
      await throwProblem(response);
    }
    const contentType = response.headers.get("Content-Type")?.split(";", 1)[0].trim().toLowerCase();
    if (contentType !== "application/pdf") throw invalidSuccessProblem(`${apiPath("drafts", draftId, "report")}.pdf`);
    return {
      blob: await response.blob(),
      filename: parseFilename(response.headers.get("Content-Disposition"), `${draftId}.pdf`)
    };
  }
}
