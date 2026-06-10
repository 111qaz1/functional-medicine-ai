import {
  AssistantCaseChatResponse,
  AssistantChatHistoryMessage,
  AnalysisMode,
  AuthMeResponse,
  AuthResponse,
  CaseDetailResponse,
  CaseSummary,
  ClinicalSummaryImageImportResult,
  ClinicianRule,
  LLMConfig,
  ParsingReviewInput,
  ProductRule,
  Questionnaire,
  RecommendationDraft,
  ReviewDecision,
  RuleScope,
  WorkspaceScope
} from "./types";

function getApiBaseUrl(): string {
  if (process.env.NEXT_PUBLIC_API_BASE_URL) {
    return process.env.NEXT_PUBLIC_API_BASE_URL;
  }

  if (typeof window !== "undefined") {
    const protocol = window.location.protocol === "https:" ? "https:" : "http:";
    return `${protocol}//${window.location.hostname}:8000`;
  }

  return "http://127.0.0.1:8000";
}

export function getPdfReportUrl(draftId: string): string {
  return `${getApiBaseUrl()}/drafts/${draftId}/report.pdf`;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers ?? {})
    },
    cache: "no-store"
  });

  if (!response.ok) {
    const rawMessage = await response.text();
    let message = rawMessage;
    try {
      const payload = JSON.parse(rawMessage) as { detail?: unknown };
      if (typeof payload.detail === "string" && payload.detail.trim()) {
        message = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        message = payload.detail
          .map((item) => (typeof item === "string" ? item : JSON.stringify(item)))
          .join("；");
      }
    } catch {
      // Keep the raw response text for non-JSON errors.
    }
    throw new Error(message || "Request failed");
  }

  return (await response.json()) as T;
}

export async function fetchCurrentUser(): Promise<AuthMeResponse> {
  return apiFetch<AuthMeResponse>("/auth/me");
}

export async function registerDoctor(username: string, password: string, displayName?: string) {
  return apiFetch<AuthResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password, display_name: displayName || null })
  });
}

export async function loginDoctor(username: string, password: string) {
  return apiFetch<AuthResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password })
  });
}

export async function logoutDoctor() {
  return apiFetch<{ logged_out: boolean }>("/auth/logout", {
    method: "POST"
  });
}

export async function fetchCases(workspace: WorkspaceScope = "public"): Promise<CaseSummary[]> {
  const payload = await apiFetch<{ cases: CaseSummary[] }>(`/cases?workspace=${workspace}`);
  return payload.cases;
}

export async function createCase(
  customer_name: string,
  consultant_id?: string,
  analysis_mode: AnalysisMode = "llm_primary",
  workspace_scope: WorkspaceScope = "public"
) {
  return apiFetch<CaseDetailResponse>("/cases", {
    method: "POST",
    body: JSON.stringify({ customer_name, consultant_id, analysis_mode, workspace_scope })
  });
}

export async function deleteCase(caseId: string) {
  return apiFetch<{ deleted: boolean; case_id: string }>(`/cases/${caseId}`, {
    method: "DELETE"
  });
}

export async function fetchCase(caseId: string) {
  return apiFetch<CaseDetailResponse>(`/cases/${caseId}`);
}

export async function uploadCaseFile(caseId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<CaseDetailResponse>(`/cases/${caseId}/files`, {
    method: "POST",
    body: formData
  });
}

export async function reparseCaseFile(caseId: string, fileId: string) {
  return apiFetch<CaseDetailResponse>(`/cases/${caseId}/files/${fileId}:reparse`, {
    method: "POST"
  });
}

export async function submitQuestionnaire(caseId: string, questionnaire: Questionnaire) {
  return apiFetch<CaseDetailResponse>(`/cases/${caseId}/questionnaire`, {
    method: "POST",
    body: JSON.stringify(questionnaire)
  });
}

export async function uploadQuestionnaireFile(caseId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<CaseDetailResponse>(`/cases/${caseId}/questionnaire-file`, {
    method: "POST",
    body: formData
  });
}

export async function updateClinicalSummary(caseId: string, clinical_summary_text: string) {
  return apiFetch<CaseDetailResponse>(`/cases/${caseId}/clinical-summary`, {
    method: "PUT",
    body: JSON.stringify({ clinical_summary_text })
  });
}

export async function uploadClinicalSummaryImage(caseId: string, file: File) {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<ClinicalSummaryImageImportResult>(`/cases/${caseId}/clinical-summary-image`, {
    method: "POST",
    body: formData
  });
}

export async function saveParsingReview(caseId: string, payload: ParsingReviewInput) {
  return apiFetch<CaseDetailResponse>(`/cases/${caseId}/parsing-review`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function generateDraft(caseId: string, requestedBy: string) {
  return apiFetch<RecommendationDraft>(`/cases/${caseId}/drafts:generate`, {
    method: "POST",
    body: JSON.stringify({ requested_by: requestedBy })
  });
}

export async function approveDraft(
  draftId: string,
  reviewerId: string,
  publishableSummary?: string,
  edits?: Record<string, unknown>
) {
  return apiFetch<ReviewDecision>(`/drafts/${draftId}/approve`, {
    method: "POST",
    body: JSON.stringify({
      reviewer_id: reviewerId,
      publishable_summary: publishableSummary,
      edits: edits ?? {}
    })
  });
}

export async function fetchProductCatalog() {
  const payload = await apiFetch<{ products: ProductRule[] }>("/catalog/products");
  return payload.products;
}

export async function createProductRule(product: ProductRule) {
  return apiFetch<ProductRule>("/catalog/products", {
    method: "POST",
    body: JSON.stringify(product)
  });
}

export async function updateProductRule(product: ProductRule) {
  return apiFetch<ProductRule>(`/catalog/products/${product.sku_id}`, {
    method: "PUT",
    body: JSON.stringify(product)
  });
}

export async function deleteProductRule(skuId: string) {
  return apiFetch<{ deleted: boolean; sku_id: string }>(`/catalog/products/${skuId}`, {
    method: "DELETE"
  });
}

export async function createClinicianRuleFromCase(
  caseId: string,
  instructionText: string,
  scope: RuleScope = "public"
) {
  return apiFetch<ClinicianRule>("/assistant/rules/from-case", {
    method: "POST",
    body: JSON.stringify({
      case_id: caseId,
      instruction_text: instructionText,
      scope
    })
  });
}

export async function requestAssistantChat(
  caseId: string,
  message: string,
  history: AssistantChatHistoryMessage[]
) {
  return apiFetch<AssistantCaseChatResponse>(`/assistant/cases/${caseId}/chat`, {
    method: "POST",
    body: JSON.stringify({
      message,
      history
    })
  });
}

export async function fetchClinicianRules() {
  const payload = await apiFetch<{ rules: ClinicianRule[] }>("/assistant/rules");
  return payload.rules;
}

export async function updateClinicianRule(rule: ClinicianRule) {
  return apiFetch<ClinicianRule>(`/assistant/rules/${rule.id}`, {
    method: "PUT",
    body: JSON.stringify({
      title: rule.title,
      instruction_text: rule.instruction_text,
      enabled: rule.enabled,
      action: rule.action,
      scope: rule.scope,
      strength: rule.strength,
      target_sku_ids: rule.target_sku_ids,
      trigger_marker_rules: rule.trigger_marker_rules,
      trigger_support_profiles: rule.trigger_support_profiles,
      trigger_goals: rule.trigger_goals,
      trigger_symptoms: rule.trigger_symptoms,
      trigger_chief_concerns: rule.trigger_chief_concerns,
      trigger_conditions: rule.trigger_conditions,
      notes: rule.notes ?? null
    })
  });
}

export async function deleteClinicianRule(ruleId: string) {
  return apiFetch<{ deleted: boolean; rule_id: string }>(`/assistant/rules/${ruleId}`, {
    method: "DELETE"
  });
}

export async function fetchLlmConfig() {
  return apiFetch<LLMConfig>("/system/llm-config");
}

export async function updateLlmConfig(config: Omit<LLMConfig, "configured">) {
  return apiFetch<LLMConfig>("/system/llm-config", {
    method: "PUT",
    body: JSON.stringify(config)
  });
}
