from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CaseStatus(str, Enum):
    intake = "intake"
    files_received = "files_received"
    parsing_completed = "parsing_completed"
    ready_for_recommendation = "ready_for_recommendation"
    draft_generated = "draft_generated"
    under_review = "under_review"
    approved = "approved"


class DraftStatus(str, Enum):
    pending_review = "pending_review"
    approved = "approved"
    abstained = "abstained"


class AnalysisMode(str, Enum):
    local_grounded = "local_grounded"
    llm_primary = "llm_primary"


class WorkspaceScope(str, Enum):
    public = "public"
    doctor = "doctor"


class RuleScope(str, Enum):
    public = "public"
    private = "private"


class DoctorRole(str, Enum):
    admin = "admin"
    doctor = "doctor"


class AbnormalFlag(str, Enum):
    low = "low"
    normal = "normal"
    high = "high"
    unknown = "unknown"


class IndicatorStatus(str, Enum):
    normal = "normal"
    attention = "attention"
    positive = "positive"
    info = "info"


class FileParseStatus(str, Enum):
    pending = "pending"
    parsed = "parsed"
    reviewed = "reviewed"
    failed = "failed"


class ReviewStatus(str, Enum):
    reviewed = "reviewed"
    reference_only = "reference_only"
    pending = "pending"


class ClinicianRuleAction(str, Enum):
    boost = "boost"
    avoid = "avoid"


class ExtractStatus(str, Enum):
    not_started = "not_started"
    partial = "partial"
    completed = "completed"
    failed = "failed"


class SourceSpan(StrictModel):
    file_id: str | None = None
    file_name: str
    page: int = 1
    line_number: int | None = None
    snippet: str


class ReferenceRange(StrictModel):
    lower: float | None = None
    upper: float | None = None
    raw: str | None = None


class ExtractedLabItem(StrictModel):
    marker_code: str
    marker_name: str
    raw_name: str | None = None
    raw_value: str | None = None
    value: float | None = None
    unit: str | None = None
    normalized_value: float | None = None
    normalized_unit: str | None = None
    ref_range: ReferenceRange = Field(default_factory=ReferenceRange)
    abnormal_flag: AbnormalFlag = AbnormalFlag.unknown
    confidence: float = 0.0
    source_span: SourceSpan


class CaseIndicator(StrictModel):
    indicator_name: str
    result_text: str
    status: IndicatorStatus = IndicatorStatus.info
    category: str = "case_text"
    source_span: SourceSpan


class ConsentRecord(StrictModel):
    accepted_terms: bool = True
    accepted_medical_disclaimer: bool = True
    accepted_privacy_policy: bool = True
    accepted_at: datetime = Field(default_factory=utc_now)
    accepted_by: str | None = None


class Questionnaire(StrictModel):
    age: int | None = None
    sex: Literal["female", "male", "other", "unknown"] = "unknown"
    chief_concerns: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    known_conditions: list[str] = Field(default_factory=list)
    family_history: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    food_sensitivities: list[str] = Field(default_factory=list)
    pregnant_or_lactating: bool | None = None
    diet_pattern: str | None = None
    work_pattern: str | None = None
    sitting_hours_per_day: float | None = None
    dining_out_frequency: str | None = None
    seafood_intake_ratio: str | None = None
    red_meat_intake_ratio: str | None = None
    supplement_use: str | None = None
    chemical_sensitivity: str | None = None
    sleep_hours: float | None = None
    sleep_quality: str | None = None
    exercise_frequency: str | None = None
    bowel_habits: str | None = None
    stress_level: Literal["low", "medium", "high"] | None = None
    emotional_state: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    msq_system_scores: dict[str, int] = Field(default_factory=dict)
    additional_notes: str | None = None
    form_version: str = "msq_v1"
    completed_at: datetime = Field(default_factory=utc_now)


class UploadedFile(StrictModel):
    id: str
    case_id: str
    filename: str
    content_type: str
    size_bytes: int
    storage_uri: str | None = None
    uploaded_at: datetime = Field(default_factory=utc_now)
    raw_extracted_text: str | None = None
    corrected_text: str | None = None
    source_spans: list[SourceSpan] = Field(default_factory=list)
    parse_confidence: float | None = None
    parse_status: FileParseStatus = FileParseStatus.pending
    needs_manual_review: bool = True
    missing_fields: list[str] = Field(default_factory=list)


class CaseRecord(StrictModel):
    id: str
    customer_name: str
    consultant_id: str | None = None
    workspace_scope: WorkspaceScope = WorkspaceScope.public
    owner_doctor_id: str | None = None
    analysis_mode: AnalysisMode = AnalysisMode.llm_primary
    status: CaseStatus = CaseStatus.intake
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    notes: str | None = None
    clinical_summary_text: str | None = None
    consent: ConsentRecord | None = None
    files: list[UploadedFile] = Field(default_factory=list)
    questionnaire: Questionnaire | None = None
    extracted_lab_items: list[ExtractedLabItem] = Field(default_factory=list)
    manual_indicators: list[CaseIndicator] = Field(default_factory=list)
    draft_ids: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    parsing_review_completed: bool = False
    parsing_reviewed_at: datetime | None = None
    parsing_reviewed_by: str | None = None
    parsing_missing_fields: list[str] = Field(default_factory=list)
    parsing_review_notes: str | None = None


class KnowledgeStatement(StrictModel):
    statement_id: str
    topic: str
    normalized_text: str
    evidence_level: str
    source_doc_id: str
    source_path: str | None = None
    source_type: str = "local_text"
    review_status: ReviewStatus = ReviewStatus.reviewed
    reviewed_by: str
    version: str
    tags: list[str] = Field(default_factory=list)
    topic_tags: list[str] = Field(default_factory=list)
    related_markers: list[str] = Field(default_factory=list)
    related_goals: list[str] = Field(default_factory=list)
    related_skus: list[str] = Field(default_factory=list)
    lifestyle_actions: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)


class KnowledgeManifestEntry(StrictModel):
    entry_id: str
    relative_path: str
    source_type: str
    topic: str
    extract_status: ExtractStatus = ExtractStatus.not_started
    review_status: ReviewStatus = ReviewStatus.reference_only
    tags: list[str] = Field(default_factory=list)


class ProductRule(StrictModel):
    sku_id: str
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


class ClinicianRule(StrictModel):
    id: str
    title: str
    instruction_text: str
    source_case_id: str | None = None
    created_by: str
    scope: RuleScope = RuleScope.public
    owner_doctor_id: str | None = None
    created_by_doctor_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    enabled: bool = True
    action: ClinicianRuleAction = ClinicianRuleAction.boost
    strength: float = 1.0
    target_sku_ids: list[str] = Field(default_factory=list)
    trigger_marker_rules: list[str] = Field(default_factory=list)
    trigger_support_profiles: list[str] = Field(default_factory=list)
    trigger_goals: list[str] = Field(default_factory=list)
    trigger_symptoms: list[str] = Field(default_factory=list)
    trigger_chief_concerns: list[str] = Field(default_factory=list)
    trigger_conditions: list[str] = Field(default_factory=list)
    notes: str | None = None


class DraftRecommendationItem(StrictModel):
    sku_id: str
    display_name: str
    dosage: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_details: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RecommendationDraft(StrictModel):
    id: str
    case_id: str
    status: DraftStatus = DraftStatus.pending_review
    case_summary: list[str] = Field(default_factory=list)
    key_lab_highlights: list[str] = Field(default_factory=list)
    recommended_skus: list[DraftRecommendationItem] = Field(default_factory=list)
    lifestyle_actions: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_details: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    abstain_reason: str | None = None
    manual_review_required: bool = True
    red_flags: list[str] = Field(default_factory=list)
    report_sections: dict[str, Any] = Field(default_factory=dict)
    model_version: str
    prompt_version: str
    rule_version: str
    generated_at: datetime = Field(default_factory=utc_now)


class ReviewDecision(StrictModel):
    draft_id: str
    reviewer_id: str
    edits: dict[str, Any] = Field(default_factory=dict)
    final_status: DraftStatus
    publishable_report: str
    pdf_report_path: str | None = None
    pdf_report_filename: str | None = None
    audit_log_id: str
    approved_at: datetime = Field(default_factory=utc_now)


class AuditLog(StrictModel):
    id: str
    entity_type: str
    entity_id: str
    action: str
    actor_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class DoctorAccount(StrictModel):
    id: str
    username: str
    display_name: str
    password_hash: str
    role: DoctorRole = DoctorRole.doctor
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SessionRecord(StrictModel):
    id: str
    doctor_id: str
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
