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


class FileIntakeStatus(str, Enum):
    uploaded = "uploaded"
    suspected_irrelevant = "suspected_irrelevant"
    invalid = "invalid"


class AnalysisStatus(str, Enum):
    queued = "queued"
    preparing = "preparing"
    analyzing_documents = "analyzing_documents"
    synthesizing = "synthesizing"
    validating = "validating"
    ready_for_review = "ready_for_review"
    reviewed = "reviewed"
    stale = "stale"
    failed = "failed"


class FinalGenerationStatus(str, Enum):
    idle = "idle"
    queued = "queued"
    final_synthesizing = "final_synthesizing"
    validating_support_needs = "validating_support_needs"
    mapping_products = "mapping_products"
    checking_safety = "checking_safety"
    generating_draft = "generating_draft"
    ready = "ready"
    failed = "failed"


class SemanticEvidenceStrength(str, Enum):
    direct = "direct"
    explicit_conclusion = "explicit_conclusion"
    contextual = "contextual"


class ClinicalEvidenceClass(str, Enum):
    clinical_confirmed = "clinical_confirmed"
    lab_abnormal = "lab_abnormal"
    symptom = "symptom"
    exposure = "exposure"
    genetic_risk = "genetic_risk"
    follow_up_only = "follow_up_only"


class SupportEligibilityStatus(str, Enum):
    eligible = "eligible"
    narrative_only = "narrative_only"
    rejected = "rejected"


class SupportDirection(str, Enum):
    increase = "increase"
    decrease = "decrease"
    maintain = "maintain"
    balance = "balance"
    restore = "restore"
    unknown = "unknown"


class EvidenceStatus(str, Enum):
    verified_text = "verified_text"
    needs_review = "needs_review"
    visual_model_only = "visual_model_only"


class FindingStandardizationStatus(str, Enum):
    unprocessed = "unprocessed"
    proposed = "proposed"
    validated = "validated"
    support_mapped = "support_mapped"
    system_mapped = "system_mapped"
    unmapped = "unmapped"
    rejected = "rejected"


class ReviewStatus(str, Enum):
    reviewed = "reviewed"
    reference_only = "reference_only"
    pending = "pending"


class ClinicianRuleAction(str, Enum):
    boost = "boost"
    avoid = "avoid"


class SafetyRuleAction(str, Enum):
    exclude = "exclude"
    requires_review = "requires_review"
    warn = "warn"


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


class ConfirmedClinicalFinding(StrictModel):
    finding_id: str
    finding_code: str | None = None
    finding_name: str
    system_ids: list[str] = Field(default_factory=list)
    support_goals: list[str] = Field(default_factory=list)
    support_direction: SupportDirection = SupportDirection.unknown
    mapping_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_class: ClinicalEvidenceClass = ClinicalEvidenceClass.clinical_confirmed
    standardization_status: FindingStandardizationStatus = FindingStandardizationStatus.validated
    abnormal_flag: str = "positive"
    confidence: float = 0.0
    source_span: SourceSpan


class PageText(StrictModel):
    page: int
    text: str


class AbnormalFinding(StrictModel):
    id: str
    name: str
    result_text: str | None = None
    raw_value: str | None = None
    unit: str | None = None
    reference_range: str | None = None
    abnormal_flag: str = "unknown"
    interpretation: str | None = None
    report_explanation: str | None = None
    neutral_interpretation: str | None = None
    support_need_text: str | None = None
    source_file_id: str
    source_file_name: str
    source_page: int
    source_text: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_status: EvidenceStatus = EvidenceStatus.needs_review
    evidence_notes: list[str] = Field(default_factory=list)
    marker_code_candidate: str | None = None
    finding_code_candidate: str | None = None
    system_id_candidates: list[str] = Field(default_factory=list)
    support_goal_candidates: list[str] = Field(default_factory=list)
    mapping_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    marker_code: str | None = None
    finding_code: str | None = None
    system_ids: list[str] = Field(default_factory=list)
    support_goals: list[str] = Field(default_factory=list)
    standardization_status: FindingStandardizationStatus = FindingStandardizationStatus.unprocessed
    standardization_notes: list[str] = Field(default_factory=list)


class ChronicFoodSensitivityResult(StrictModel):
    source_file_id: str
    source_file_name: str
    source_page: int = 1
    mild_foods: list[str] = Field(default_factory=list)
    moderate_foods: list[str] = Field(default_factory=list)
    high_foods: list[str] = Field(default_factory=list)
    interpretations: list[str] = Field(default_factory=list)
    valid: bool = False
    warning: str | None = None


class DocumentAnalysisResult(StrictModel):
    file_id: str
    file_name: str
    report_type: str = "unknown_medical"
    medical_content: bool = True
    summary: str | None = None
    abnormal_findings: list[AbnormalFinding] = Field(default_factory=list)
    system_findings: list[str] = Field(default_factory=list)
    questionnaire: dict[str, Any] | None = None
    food_sensitivity: ChronicFoodSensitivityResult | None = None
    warnings: list[str] = Field(default_factory=list)


class SemanticEvidenceReference(StrictModel):
    ref: str
    evidence_strength: SemanticEvidenceStrength


class SemanticSupportNeed(StrictModel):
    id: str
    support_need_text: str
    support_goal_code: str | None = None
    support_direction: SupportDirection = SupportDirection.unknown
    system_id: str
    evidence_refs: list[SemanticEvidenceReference] = Field(default_factory=list)
    evidence_strength: SemanticEvidenceStrength = SemanticEvidenceStrength.contextual
    evidence_class: ClinicalEvidenceClass = ClinicalEvidenceClass.symptom
    corroboration_count: int = Field(default=0, ge=0)
    rationale: str
    model_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    eligibility_status: SupportEligibilityStatus = SupportEligibilityStatus.narrative_only
    validation_notes: list[str] = Field(default_factory=list)


class CaseAnalysis(StrictModel):
    id: str
    case_id: str
    version: int = 1
    status: AnalysisStatus = AnalysisStatus.queued
    snapshot_hash: str
    file_ids: list[str] = Field(default_factory=list)
    model_version: str
    prompt_version: str = "case-analysis-v1"
    standardization_version: str = "legacy"
    progress_current: int = 0
    progress_total: int = 0
    current_file_name: str | None = None
    document_results: list[DocumentAnalysisResult] = Field(default_factory=list)
    case_summary: str | None = None
    reviewed_case_summary: str | None = None
    system_findings: list[str] = Field(default_factory=list)
    reviewed_system_findings: list[str] = Field(default_factory=list)
    abnormal_findings: list[AbnormalFinding] = Field(default_factory=list)
    reviewed_abnormal_findings: list[AbnormalFinding] = Field(default_factory=list)
    questionnaire: Questionnaire | None = None
    food_sensitivity: ChronicFoodSensitivityResult | None = None
    ignored_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    revision: int = 1
    draft_id: str | None = None
    final_generation_status: FinalGenerationStatus = FinalGenerationStatus.idle
    final_generation_progress: int = Field(default=0, ge=0, le=100)
    final_generation_error: str | None = None
    final_generation_revision: int = 0
    support_goal_version: str = "legacy"
    support_needs: list[SemanticSupportNeed] = Field(default_factory=list)
    final_structured_system_findings: list["StructuredSystemFinding"] = Field(default_factory=list)
    final_synthesis_completed_revision: int | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


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
    content_sha256: str | None = None
    intake_status: FileIntakeStatus = FileIntakeStatus.uploaded
    page_count: int = 0
    page_texts: list[PageText] = Field(default_factory=list)
    is_scanned: bool = False
    precheck_warning: str | None = None
    validation_error: str | None = None


class CaseRecord(StrictModel):
    id: str
    customer_name: str
    consultant_id: str | None = None
    workspace_scope: WorkspaceScope = WorkspaceScope.public
    owner_doctor_id: str | None = None
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
    confirmed_clinical_findings: list[ConfirmedClinicalFinding] = Field(default_factory=list)
    draft_ids: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    parsing_review_completed: bool = False
    parsing_reviewed_at: datetime | None = None
    parsing_reviewed_by: str | None = None
    parsing_missing_fields: list[str] = Field(default_factory=list)
    parsing_review_notes: str | None = None
    latest_analysis_id: str | None = None
    # Read-only compatibility with records written by the abandoned PR #13.
    specialty_reports: list[dict[str, Any]] = Field(default_factory=list)
    parsing_revision: int = 0


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


class DosageRegimen(StrictModel):
    unit: str = "粒"
    single_dose_min: float | None = None
    single_dose_max: float | None = None
    daily_frequency_min: float | None = None
    daily_frequency_max: float | None = None
    weekly_frequency_min: float | None = None
    weekly_frequency_max: float | None = None
    timing: list[str] = Field(default_factory=list)
    interval_hours_min: float | None = None
    interval_hours_max: float | None = None
    daily_max: float | None = None
    duration: str | None = None
    maintenance: str | None = None


class DosageOptionSummary(StrictModel):
    option_id: str
    label: str
    display_text: str
    requires_review: bool = False
    regimen: DosageRegimen = Field(default_factory=DosageRegimen)


class DraftRecommendationItem(StrictModel):
    sku_id: str
    display_name: str
    dosage: str
    dosage_option_id: str | None = None
    dosage_option_label: str | None = None
    dosage_match_reasons: list[str] = Field(default_factory=list)
    dosage_options: list[DosageOptionSummary] = Field(default_factory=list)
    dosage_regimen: DosageRegimen | None = None
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_details: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    primary_system_id: str | None = None
    matched_finding_ids: list[str] = Field(default_factory=list)
    matched_support_need_ids: list[str] = Field(default_factory=list)
    system_priority_rank: int | None = None
    safety_decisions: list["SafetyDecision"] = Field(default_factory=list)


class SafetyDecision(StrictModel):
    rule_id: str
    sku_id: str | None = None
    action: SafetyRuleAction
    message: str
    source_ref: str | None = None


class StructuredSystemFinding(StrictModel):
    system_id: str
    system_name: str
    priority_level: str
    priority_score: float
    summary: str
    finding_ids: list[str] = Field(default_factory=list)


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
    safety_decisions: list[SafetyDecision] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    abstain_reason: str | None = None
    manual_review_required: bool = True
    red_flags: list[str] = Field(default_factory=list)
    structured_system_findings: list[StructuredSystemFinding] = Field(default_factory=list)
    report_sections: dict[str, Any] = Field(default_factory=dict)
    internal_audit: dict[str, Any] = Field(default_factory=dict)
    model_version: str
    prompt_version: str
    rule_version: str
    generated_at: datetime = Field(default_factory=utc_now)
    source_analysis_id: str | None = None
    source_analysis_revision: int | None = None
    source_snapshot_hash: str | None = None
    support_goal_version: str = "legacy"
    # Read-only compatibility with drafts written by the abandoned PR #13.
    updated_at: datetime | None = None
    revision: int = 1
    last_edited_by: str | None = None
    last_edit_reason: str | None = None


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
