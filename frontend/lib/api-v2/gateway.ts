import type {
  AnalysisResponse,
  ApprovalRequest,
  ApprovalResponse,
  AttachmentBatchResponse,
  AttachmentType,
  CaseCreateRequest,
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

export class HttpWorkflowGateway implements WorkflowGateway {
  constructor(private readonly fetchImpl: typeof fetch = fetch) {}

  private async json<T>(url: string, init?: RequestInit): Promise<{ data: T; response: Response }> {
    const response = await this.fetchImpl(url, {
      cache: "no-store",
      ...init,
      headers: {
        Accept: "application/json, application/problem+json",
        ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
        ...init?.headers
      }
    });
    if (!response.ok) await throwProblem(response);
    return { data: (await response.json()) as T, response };
  }

  async createCase(payload: CaseCreateRequest): Promise<CaseResponse> {
    return (await this.json<CaseResponse>(apiPath("cases"), { method: "POST", body: JSON.stringify(payload) })).data;
  }

  async getCase(caseId: string): Promise<CaseResponse> {
    return (await this.json<CaseResponse>(apiPath("cases", caseId))).data;
  }

  async updateClinicalSummary(caseId: string, payload: ClinicalSummaryUpdateRequest): Promise<CaseResponse> {
    return (await this.json<CaseResponse>(apiPath("cases", caseId, "clinical-summary"), {
      method: "PUT",
      body: JSON.stringify(payload)
    })).data;
  }

  async uploadAttachments(caseId: string, attachmentType: AttachmentType, files: File[]): Promise<AttachmentBatchResponse> {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    form.append("attachment_type", attachmentType);
    return (await this.json<AttachmentBatchResponse>(apiPath("cases", caseId, "attachments"), {
      method: "POST",
      body: form
    })).data;
  }

  async startAnalysis(caseId: string, payload: StartAnalysisRequest): Promise<AcceptedOperation> {
    const { data, response } = await this.json<OperationResponse>(apiPath("cases", caseId, "analyses"), {
      method: "POST",
      body: JSON.stringify(payload)
    });
    return { operation: data, location: response.headers.get("Location") };
  }

  async getOperation(operationId: string, signal?: AbortSignal): Promise<OperationResponse> {
    return (await this.json<OperationResponse>(apiPath("operations", operationId), { signal })).data;
  }

  async getLatestAnalysis(caseId: string): Promise<AnalysisResponse> {
    return (await this.json<AnalysisResponse>(apiPath("cases", caseId, "analyses", "latest"))).data;
  }

  async submitReview(caseId: string, analysisId: string, payload: ReviewSubmitRequest): Promise<AcceptedOperation> {
    const { data, response } = await this.json<OperationResponse>(
      apiPath("cases", caseId, "analyses", analysisId, "reviews"),
      { method: "POST", body: JSON.stringify(payload) }
    );
    return { operation: data, location: response.headers.get("Location") };
  }

  async retryDraftGeneration(caseId: string, analysisId: string): Promise<AcceptedOperation> {
    const { data, response } = await this.json<OperationResponse>(
      `${apiPath("cases", caseId, "analyses", analysisId, "draft-generation")}:retry`,
      { method: "POST" }
    );
    return { operation: data, location: response.headers.get("Location") };
  }

  async getDraft(draftId: string): Promise<DraftResponse> {
    return (await this.json<DraftResponse>(apiPath("drafts", draftId))).data;
  }

  async approveDraft(draftId: string, payload: ApprovalRequest): Promise<ApprovalResponse> {
    return (await this.json<ApprovalResponse>(apiPath("drafts", draftId, "approval"), {
      method: "POST",
      body: JSON.stringify(payload)
    })).data;
  }

  async getReport(draftId: string): Promise<ReportResponse> {
    return (await this.json<ReportResponse>(apiPath("drafts", draftId, "report"))).data;
  }

  async downloadReport(draftId: string): Promise<DownloadedReport> {
    const response = await this.fetchImpl(`${apiPath("drafts", draftId, "report")}.pdf`, {
      cache: "no-store",
      headers: { Accept: "application/pdf, application/problem+json" }
    });
    if (!response.ok) await throwProblem(response);
    return {
      blob: await response.blob(),
      filename: parseFilename(response.headers.get("Content-Disposition"), `${draftId}.pdf`)
    };
  }
}
