from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CaseStatusValue = Literal[
    "intake",
    "files_received",
    "parsing_completed",
    "ready_for_recommendation",
    "draft_generated",
    "under_review",
    "approved",
]
IntakeStatusValue = Literal["uploaded", "suspected_irrelevant", "invalid"]
ParseStatusValue = Literal["pending", "parsed", "reviewed", "failed"]
AnalysisStatusValue = Literal[
    "queued",
    "preparing",
    "analyzing_documents",
    "synthesizing",
    "validating",
    "ready_for_review",
    "reviewed",
    "stale",
    "failed",
]
DraftGenerationStatusValue = Literal[
    "idle",
    "queued",
    "final_synthesizing",
    "validating_support_needs",
    "mapping_products",
    "checking_safety",
    "generating_draft",
    "ready",
    "failed",
]
DraftStatusValue = Literal["pending_review", "approved", "abstained"]


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def strip_scalar_strings(cls, value):
        if not isinstance(value, dict):
            return value
        return {
            key: item.strip() if key != "op" and isinstance(item, str) else item
            for key, item in value.items()
        }


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationIssue(ContractModel):
    location: list[str | int] = Field(default_factory=list)
    message: str
    error_type: str


class ProblemDetails(ContractModel):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    errors: list[ValidationIssue] = Field(default_factory=list)


class CaseCreateRequest(StrictRequestModel):
    customer_name: str = Field(min_length=1, max_length=160)
    consultant_id: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=4000)


class ClinicalSummaryUpdateRequest(StrictRequestModel):
    clinical_summary: str | None = Field(default=None, max_length=20000)


class AttachmentResponse(ContractModel):
    id: str
    filename: str
    attachment_type: Literal["medical_record"] = "medical_record"
    media_type: str
    size_bytes: int
    uploaded_at: datetime
    intake_status: IntakeStatusValue
    parse_status: ParseStatusValue
    parse_confidence: float | None = None
    needs_manual_review: bool
    missing_fields: list[str] = Field(default_factory=list)
    page_count: int
    is_scanned: bool
    warning: str | None = None
    error: str | None = None


class CaseResponse(ContractModel):
    id: str
    customer_name: str
    consultant_id: str | None = None
    status: CaseStatusValue
    notes: str | None = None
    clinical_summary: str | None = None
    created_at: datetime
    updated_at: datetime
    attachments: list[AttachmentResponse] = Field(default_factory=list)


class CaseSummaryResponse(ContractModel):
    id: str
    customer_name: str
    consultant_id: str | None = None
    status: CaseStatusValue
    attachment_count: int = 0
    created_at: datetime
    updated_at: datetime


class CaseListResponse(ContractModel):
    items: list[CaseSummaryResponse] = Field(default_factory=list)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)


class AttachmentFailure(ContractModel):
    code: str
    message: str
    retryable: bool = False


class AttachmentUploadItem(ContractModel):
    file_id: str | None = None
    filename: str
    attachment_type: Literal["medical_record", "questionnaire"]
    status: Literal[
        "parsed",
        "pending",
        "questionnaire_imported",
        "duplicate",
        "failed",
    ]
    media_type: str
    size_bytes: int
    parse_status: ParseStatusValue | None = None
    lab_item_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    failure: AttachmentFailure | None = None


class AttachmentBatchMeta(ContractModel):
    case_id: str
    case_status: CaseStatusValue
    accepted_count: int
    failed_count: int


class AttachmentBatchResponse(ContractModel):
    items: list[AttachmentUploadItem] = Field(default_factory=list)
    meta: AttachmentBatchMeta


class StartAnalysisRequest(StrictRequestModel):
    third_party_processing_confirmed: bool = False


class OperationProgress(ContractModel):
    current: int = 0
    total: int = 0
    percent: int = Field(default=0, ge=0, le=100)
    current_item: str | None = None


class OperationFailure(ContractModel):
    code: str
    message: str
    retryable: bool = False


class OperationResponse(ContractModel):
    operation_id: str
    kind: Literal["case_workflow"] = "case_workflow"
    stage: Literal["analysis", "draft_generation"]
    status: Literal["queued", "running", "succeeded", "failed"]
    case_id: str
    analysis_id: str
    draft_id: str | None = None
    progress: OperationProgress
    failure: OperationFailure | None = None
    created_at: datetime
    updated_at: datetime


class FindingResponse(ContractModel):
    id: str
    name: str
    result_text: str | None = None
    raw_value: str | None = None
    unit: str | None = None
    reference_range: str | None = None
    abnormal_flag: str
    interpretation: str | None = None
    report_explanation: str | None = None
    neutral_interpretation: str | None = None
    support_need_text: str | None = None
    source_file_id: str
    source_file_name: str
    source_page: int
    source_text: str
    confidence: float
    evidence_status: str
    evidence_notes: list[str] = Field(default_factory=list)
    observed_at: datetime | None = None


class SupplementResponse(ContractModel):
    id: str
    name: str
    source_file_ids: list[str] = Field(default_factory=list)
    source_file_names: list[str] = Field(default_factory=list)
    doctor_added: bool


class FoodSensitivityItemResponse(ContractModel):
    id: str
    name: str
    raw_value: str | None = None
    unit: str | None = None
    abnormal_flag: str
    severity: Literal["mild", "moderate", "high", "ungraded"]
    reported_grade: str | None = None
    reported_grade_meaning: str | None = None
    reference_range: str | None = None
    grading_basis: str | None = None
    source_page: int
    source_text: str
    evidence_status: str


class FoodSensitivityResponse(ContractModel):
    source_file_id: str
    source_file_name: str
    source_page: int
    items: list[FoodSensitivityItemResponse] = Field(default_factory=list)
    interpretations: list[str] = Field(default_factory=list)
    valid: bool
    warning: str | None = None


class AnalysisProgress(ContractModel):
    current: int
    total: int
    percent: int = Field(ge=0, le=100)
    current_file_name: str | None = None


class DraftGenerationState(ContractModel):
    status: DraftGenerationStatusValue
    progress: int = Field(ge=0, le=100)
    error: str | None = None


class AnalysisResponse(ContractModel):
    id: str
    case_id: str
    version: int
    revision: int
    status: AnalysisStatusValue
    progress: AnalysisProgress
    case_summary: str | None = None
    system_findings: list[str] = Field(default_factory=list)
    abnormal_findings: list[FindingResponse] = Field(default_factory=list)
    current_supplements: list[SupplementResponse] = Field(default_factory=list)
    food_sensitivity: FoodSensitivityResponse | None = None
    warnings: list[str] = Field(default_factory=list)
    error: OperationFailure | None = None
    draft_generation: DraftGenerationState
    draft_id: str | None = None
    created_at: datetime
    updated_at: datetime


class FindingUpdateFields(StrictRequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    result_text: str | None = Field(default=None, max_length=2000)
    raw_value: str | None = Field(default=None, max_length=500)
    unit: str | None = Field(default=None, max_length=120)
    reference_range: str | None = Field(default=None, max_length=500)
    abnormal_flag: Literal["low", "normal", "high", "positive", "unknown"] | None = None
    source_file_id: str | None = Field(default=None, min_length=1)
    source_file_name: str | None = Field(default=None, min_length=1, max_length=500)
    source_page: int | None = Field(default=None, ge=1)
    source_text: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def require_change(self) -> "FindingUpdateFields":
        if not self.model_fields_set:
            raise ValueError("changes must contain at least one editable field")
        return self


class FindingAddValue(StrictRequestModel):
    name: str = Field(min_length=1, max_length=160)
    result_text: str | None = Field(default=None, max_length=2000)
    raw_value: str | None = Field(default=None, max_length=500)
    unit: str | None = Field(default=None, max_length=120)
    reference_range: str | None = Field(default=None, max_length=500)
    abnormal_flag: Literal["low", "normal", "high", "positive", "unknown"] = "unknown"
    source_file_id: str = Field(min_length=1)
    source_file_name: str = Field(min_length=1, max_length=500)
    source_page: int = Field(default=1, ge=1)
    source_text: str = Field(min_length=1, max_length=4000)


class FindingAdd(StrictRequestModel):
    op: Literal["add"]
    value: FindingAddValue


class FindingUpdate(StrictRequestModel):
    op: Literal["update"]
    id: str = Field(min_length=1)
    changes: FindingUpdateFields


class FindingRemove(StrictRequestModel):
    op: Literal["remove"]
    id: str = Field(min_length=1)


FindingChange = Annotated[FindingAdd | FindingUpdate | FindingRemove, Field(discriminator="op")]


class SupplementUpdateFields(StrictRequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def require_change(self) -> "SupplementUpdateFields":
        if not self.model_fields_set:
            raise ValueError("changes must contain at least one editable field")
        return self


class SupplementAddValue(StrictRequestModel):
    name: str = Field(min_length=1, max_length=120)


class SupplementAdd(StrictRequestModel):
    op: Literal["add"]
    value: SupplementAddValue


class SupplementUpdate(StrictRequestModel):
    op: Literal["update"]
    id: str = Field(min_length=1)
    changes: SupplementUpdateFields


class SupplementRemove(StrictRequestModel):
    op: Literal["remove"]
    id: str = Field(min_length=1)


SupplementChange = Annotated[
    SupplementAdd | SupplementUpdate | SupplementRemove,
    Field(discriminator="op"),
]


class FoodSensitivityUpdateFields(StrictRequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    raw_value: str | None = Field(default=None, max_length=500)
    unit: str | None = Field(default=None, max_length=120)
    abnormal_flag: Literal["low", "normal", "high", "positive", "unknown"] | None = None
    severity: Literal["mild", "moderate", "high", "ungraded"] | None = None
    reported_grade: str | None = Field(default=None, max_length=120)
    reported_grade_meaning: str | None = Field(default=None, max_length=500)
    reference_range: str | None = Field(default=None, max_length=500)
    grading_basis: str | None = Field(default=None, max_length=1000)
    source_page: int | None = Field(default=None, ge=1)
    source_text: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def require_change(self) -> "FoodSensitivityUpdateFields":
        if not self.model_fields_set:
            raise ValueError("changes must contain at least one editable field")
        return self


class FoodSensitivityAddValue(StrictRequestModel):
    name: str = Field(min_length=1, max_length=120)
    raw_value: str | None = Field(default=None, max_length=500)
    unit: str | None = Field(default=None, max_length=120)
    abnormal_flag: Literal["low", "normal", "high", "positive", "unknown"] = "unknown"
    severity: Literal["mild", "moderate", "high", "ungraded"] = "ungraded"
    reported_grade: str | None = Field(default=None, max_length=120)
    reported_grade_meaning: str | None = Field(default=None, max_length=500)
    reference_range: str | None = Field(default=None, max_length=500)
    grading_basis: str | None = Field(default=None, max_length=1000)
    source_file_id: str = Field(min_length=1)
    source_file_name: str = Field(min_length=1, max_length=500)
    source_page: int = Field(default=1, ge=1)
    source_text: str = Field(min_length=1, max_length=4000)


class FoodSensitivityAdd(StrictRequestModel):
    op: Literal["add"]
    value: FoodSensitivityAddValue


class FoodSensitivityUpdate(StrictRequestModel):
    op: Literal["update"]
    id: str = Field(min_length=1)
    changes: FoodSensitivityUpdateFields


class FoodSensitivityRemove(StrictRequestModel):
    op: Literal["remove"]
    id: str = Field(min_length=1)


FoodSensitivityChange = Annotated[
    FoodSensitivityAdd | FoodSensitivityUpdate | FoodSensitivityRemove,
    Field(discriminator="op"),
]


class ReviewSubmitRequest(StrictRequestModel):
    expected_revision: int = Field(ge=1)
    finding_changes: list[FindingChange] = Field(default_factory=list)
    supplement_changes: list[SupplementChange] = Field(default_factory=list)
    food_sensitivity_changes: list[FoodSensitivityChange] = Field(default_factory=list)


class DosageRegimenResponse(ContractModel):
    unit: str
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


class DosageOptionResponse(ContractModel):
    option_id: str
    label: str
    display_text: str
    requires_review: bool
    regimen: DosageRegimenResponse


class DraftRecommendationResponse(ContractModel):
    sku_id: str
    display_name: str
    dosage: str
    dosage_option_id: str | None = None
    dosage_option_label: str | None = None
    dosage_match_reasons: list[str] = Field(default_factory=list)
    dosage_options: list[DosageOptionResponse] = Field(default_factory=list)
    dosage_regimen: DosageRegimenResponse | None = None
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_details: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    current_supplement_overlap_notice: str | None = None


class SafetyDecisionResponse(ContractModel):
    rule_id: str
    sku_id: str | None = None
    action: Literal["exclude", "requires_review", "warn"]
    message: str
    source_ref: str | None = None


class StructuredSystemFindingResponse(ContractModel):
    system_id: str
    system_name: str
    priority_level: str
    priority_score: float
    summary: str
    finding_ids: list[str] = Field(default_factory=list)


class LifestyleActionResponse(ContractModel):
    action_id: str
    domain: Literal["diet", "movement", "sleep", "stress"]
    category: str
    text: str
    anchor_refs: list[str] = Field(default_factory=list)
    quantity: str | None = None
    safety_level: Literal["standard", "review", "referral"]
    clinician_review_required: bool


class LifestyleSectionResponse(ContractModel):
    domain: Literal["diet", "movement", "sleep", "stress"]
    title: str
    actions: list[LifestyleActionResponse] = Field(default_factory=list)


class LifestyleProtocolResponse(ContractModel):
    protocol_id: str
    title: str
    admission: Literal["direct", "review", "referral"]
    reason: str
    anchor_refs: list[str] = Field(default_factory=list)


class LifestylePlanResponse(ContractModel):
    status: Literal["ready", "partial", "needs_review", "blocked"]
    rule_version: str
    selected_protocols: list[LifestyleProtocolResponse] = Field(default_factory=list)
    sections: list[LifestyleSectionResponse] = Field(default_factory=list)
    monitoring: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    clinician_review_required: bool


class HealthPortraitFindingResponse(ContractModel):
    finding_id: str
    name: str
    system_ids: list[str] = Field(default_factory=list)
    evidence_level: Literal["objective_lab", "imaging_pathology", "symptom_cluster", "patient_reported"]
    display_value: str | None = None
    deviation_tier: Literal["P0", "T2", "T1", "unknown"]
    trend: Literal["worsening", "improving", "stable", "unknown"]
    food_sensitivity: bool
    intervenable: bool
    objective: bool
    organ_damage_signal: bool


class HealthPortraitRiskResponse(ContractModel):
    p0_referral: list[str] = Field(default_factory=list)
    review_required: list[str] = Field(default_factory=list)
    dose_caution: list[str] = Field(default_factory=list)


class HealthMechanismChainResponse(ContractModel):
    chain_id: str
    axis_name: str
    system_path: list[str] = Field(default_factory=list)
    display_path: list[str] = Field(default_factory=list)
    supporting_finding_ids: list[str] = Field(default_factory=list)
    auxiliary_food_sensitivity_ids: list[str] = Field(default_factory=list)
    objective_support_count: int


class HealthInterventionHubResponse(ContractModel):
    system_id: str
    label: str
    supporting_finding_ids: list[str] = Field(default_factory=list)
    chain_intersection_count: int
    upstream_score: int
    evidence_score: int
    downstream_reach: int


class HealthInterventionStepResponse(ContractModel):
    order: int
    label: str
    target_system_ids: list[str] = Field(default_factory=list)
    linked_recommendation_ids: list[str] = Field(default_factory=list)
    linked_lifestyle_action_ids: list[str] = Field(default_factory=list)


class CoreHealthPortraitDecisionResponse(ContractModel):
    findings: list[HealthPortraitFindingResponse] = Field(default_factory=list)
    risks: HealthPortraitRiskResponse
    mechanism_chains: list[HealthMechanismChainResponse] = Field(default_factory=list)
    intervention_hubs: list[HealthInterventionHubResponse] = Field(default_factory=list)
    intervention_steps: list[HealthInterventionStepResponse] = Field(default_factory=list)
    steady_state_axis: str | None = None


class CoreHealthPortraitResponse(ContractModel):
    text: str
    status: Literal["ready", "degraded", "referral_only"]
    manual_review_required: bool
    validation_violations: list[str] = Field(default_factory=list)
    decision: CoreHealthPortraitDecisionResponse
    rule_version: str


class ReportSectionResponse(ContractModel):
    title: str
    items: list[str] = Field(default_factory=list)


class DraftResponse(ContractModel):
    id: str
    case_id: str
    status: DraftStatusValue
    revision: int
    public_summary: list[str] = Field(default_factory=list)
    key_lab_highlights: list[str] = Field(default_factory=list)
    recommended_skus: list[DraftRecommendationResponse] = Field(default_factory=list)
    lifestyle_actions: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    evidence_details: list[str] = Field(default_factory=list)
    contraindications: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    confidence: float
    abstain_reason: str | None = None
    manual_review_required: bool
    red_flags: list[str] = Field(default_factory=list)
    core_health_portrait: CoreHealthPortraitResponse | None = None
    structured_system_findings: list[StructuredSystemFindingResponse] = Field(default_factory=list)
    lifestyle_plan: LifestylePlanResponse | None = None
    safety_decisions: list[SafetyDecisionResponse] = Field(default_factory=list)
    uncovered_system_ids: list[str] = Field(default_factory=list)
    uncovered_system_reasons: dict[str, Literal["no_approved_mapping", "evidence_not_eligible", "safety_excluded"]] = Field(default_factory=dict)
    report_sections: list[ReportSectionResponse] = Field(default_factory=list)
    generated_at: datetime


class DosageOverrideRequest(StrictRequestModel):
    sku_id: str = Field(min_length=1)
    option_id: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=1000)


class ApprovalRequest(StrictRequestModel):
    expected_revision: int = Field(ge=1)
    publishable_summary: str | None = Field(default=None, max_length=50000)
    excluded_sku_ids: list[str] = Field(default_factory=list)
    dosage_overrides: list[DosageOverrideRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_or_conflicting_skus(self) -> "ApprovalRequest":
        excluded = [value.strip() for value in self.excluded_sku_ids]
        override_ids = [item.sku_id.strip() for item in self.dosage_overrides]
        if any(not value for value in excluded):
            raise ValueError("excluded_sku_ids cannot contain an empty SKU")
        if len(excluded) != len(set(excluded)):
            raise ValueError("excluded_sku_ids cannot contain duplicates")
        if len(override_ids) != len(set(override_ids)):
            raise ValueError("dosage_overrides cannot contain duplicate SKUs")
        overlap = sorted(set(excluded).intersection(override_ids))
        if overlap:
            raise ValueError("an excluded SKU cannot also have a dosage override")
        return self


class ApprovalResponse(ContractModel):
    draft_id: str
    status: DraftStatusValue
    reviewer_id: str
    publishable_report: str
    approved_at: datetime
    report_ready: bool
    report_url: str


class ReportResponse(ContractModel):
    draft_id: str
    status: Literal["ready"] = "ready"
    filename: str
    download_url: str
    approved_at: datetime
