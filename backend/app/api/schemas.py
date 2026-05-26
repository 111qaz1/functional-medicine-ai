from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models import (
    AnalysisMode,
    AuditLog,
    CaseIndicator,
    CaseRecord,
    ClinicianRule,
    ClinicianRuleAction,
    ConsentRecord,
    DoctorAccount,
    DoctorRole,
    ExtractedLabItem,
    IndicatorStatus,
    KnowledgeManifestEntry,
    ProductRule,
    Questionnaire,
    RecommendationDraft,
    ReviewDecision,
    RuleScope,
    WorkspaceScope,
)


class CreateCaseRequest(BaseModel):
    customer_name: str
    consultant_id: str | None = None
    workspace_scope: WorkspaceScope = WorkspaceScope.public
    notes: str | None = None
    consent: ConsentRecord | None = None
    analysis_mode: AnalysisMode = AnalysisMode.llm_primary


class GenerateDraftRequest(BaseModel):
    requested_by: str = "system"


class ApproveDraftRequest(BaseModel):
    reviewer_id: str
    publishable_summary: str | None = None
    edits: dict[str, str] = Field(default_factory=dict)


class ParsingReviewFileRequest(BaseModel):
    file_id: str
    corrected_text: str | None = None
    missing_fields: list[str] = Field(default_factory=list)


class ManualIndicatorRequest(BaseModel):
    indicator_name: str = Field(min_length=1)
    result_text: str = Field(min_length=1)
    status: IndicatorStatus = IndicatorStatus.attention
    evidence_text: str | None = None


class ParsingReviewRequest(BaseModel):
    reviewer_id: str
    files: list[ParsingReviewFileRequest] = Field(default_factory=list)
    normalized_lab_items: list[ExtractedLabItem] = Field(default_factory=list)
    manual_indicators: list[ManualIndicatorRequest] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    review_notes: str | None = None


class ClinicalSummaryUpdateRequest(BaseModel):
    clinical_summary_text: str | None = None


class CaseSummaryResponse(BaseModel):
    id: str
    customer_name: str
    analysis_mode: AnalysisMode = AnalysisMode.llm_primary
    status: str
    consultant_id: str | None = None
    workspace_scope: WorkspaceScope = WorkspaceScope.public
    owner_doctor_id: str | None = None
    created_at: datetime
    updated_at: datetime
    file_count: int
    lab_item_count: int
    latest_draft_id: str | None = None


class DashboardResponse(BaseModel):
    cases: list[CaseSummaryResponse]


class CaseDetailResponse(BaseModel):
    case: CaseRecord
    display_indicators: list[CaseIndicator] = Field(default_factory=list)
    latest_draft: RecommendationDraft | None = None
    review_decision: ReviewDecision | None = None
    audit_logs: list[AuditLog] = Field(default_factory=list)
    matched_clinician_rules: list[ClinicianRule] = Field(default_factory=list)


class ClinicalSummaryImageImportResponse(BaseModel):
    case_detail: CaseDetailResponse
    filename: str
    extracted_text: str
    confidence: float


class QuestionnaireRequest(Questionnaire):
    pass


class KnowledgeManifestResponse(BaseModel):
    entries: list[KnowledgeManifestEntry] = Field(default_factory=list)


class ProductCatalogResponse(BaseModel):
    products: list[ProductRule] = Field(default_factory=list)


class ProductRuleUpsertFields(BaseModel):
    display_name: str
    category: str
    source_refs: list[str] = Field(default_factory=list)
    formula_summary: str
    core_ingredients: list[str] = Field(default_factory=list)
    candidate_use_cases: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    enabled: bool = True
    merge_status: str | None = None
    indications: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    dosage_rule: str
    interaction_rule: list[str] = Field(default_factory=list)
    warning_text: list[str] = Field(default_factory=list)
    lifestyle_tags: list[str] = Field(default_factory=list)
    priority: int = 50


class ProductRuleCreateRequest(ProductRuleUpsertFields):
    sku_id: str


class ProductRuleUpdateRequest(ProductRuleUpsertFields):
    pass


class ClinicianRuleListResponse(BaseModel):
    rules: list[ClinicianRule] = Field(default_factory=list)


class CreateClinicianRuleFromCaseRequest(BaseModel):
    case_id: str
    author_id: str | None = None
    instruction_text: str
    scope: RuleScope = RuleScope.public


class AssistantChatHistoryMessage(BaseModel):
    role: str
    text: str


class AssistantCaseChatRequest(BaseModel):
    message: str
    history: list[AssistantChatHistoryMessage] = Field(default_factory=list)


class AssistantCaseChatResponse(BaseModel):
    reply: str
    mode: str
    model_label: str


class UpdateClinicianRuleRequest(BaseModel):
    title: str
    instruction_text: str
    enabled: bool = True
    action: ClinicianRuleAction = ClinicianRuleAction.boost
    scope: RuleScope | None = None
    strength: float = 1.0
    target_sku_ids: list[str] = Field(default_factory=list)
    trigger_marker_rules: list[str] = Field(default_factory=list)
    trigger_support_profiles: list[str] = Field(default_factory=list)
    trigger_goals: list[str] = Field(default_factory=list)
    trigger_symptoms: list[str] = Field(default_factory=list)
    trigger_chief_concerns: list[str] = Field(default_factory=list)
    trigger_conditions: list[str] = Field(default_factory=list)
    notes: str | None = None


class LLMConfigResponse(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    api_style: str = "auto"
    timeout_seconds: float = 45.0
    temperature: float = 0.1
    configured: bool = False
    validation_error: str | None = None


class LLMConfigUpdateRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    api_style: str = "auto"
    timeout_seconds: float = 45.0
    temperature: float = 0.1


class DoctorAccountResponse(BaseModel):
    id: str
    username: str
    display_name: str
    role: DoctorRole
    enabled: bool

    @classmethod
    def from_account(cls, doctor: DoctorAccount) -> "DoctorAccountResponse":
        return cls(
            id=doctor.id,
            username=doctor.username,
            display_name=doctor.display_name,
            role=doctor.role,
            enabled=doctor.enabled,
        )


class AuthRegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str | None = None


class AuthLoginRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    doctor: DoctorAccountResponse


class AuthMeResponse(BaseModel):
    doctor: DoctorAccountResponse | None = None
