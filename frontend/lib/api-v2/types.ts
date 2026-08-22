export type IsoDateTime = string;

export type CaseStatus =
  | "intake"
  | "files_received"
  | "parsing_completed"
  | "ready_for_recommendation"
  | "draft_generated"
  | "under_review"
  | "approved";

export type IntakeStatus = "uploaded" | "suspected_irrelevant" | "invalid";
export type ParseStatus = "pending" | "parsed" | "reviewed" | "failed";
export type AttachmentType = "medical_record" | "questionnaire";
export type AbnormalFlag = "low" | "normal" | "high" | "positive" | "unknown";
export type FoodSensitivitySeverity = "mild" | "moderate" | "high" | "ungraded";

export interface ValidationIssue {
  location: Array<string | number>;
  message: string;
  error_type: string;
}

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
  code: string;
  errors: ValidationIssue[];
}

export interface CaseCreateRequest {
  customer_name: string;
  consultant_id: string | null;
  notes: string | null;
}

export interface ClinicalSummaryUpdateRequest {
  clinical_summary: string | null;
}

export interface AttachmentResponse {
  id: string;
  filename: string;
  attachment_type: "medical_record";
  media_type: string;
  size_bytes: number;
  uploaded_at: IsoDateTime;
  intake_status: IntakeStatus;
  parse_status: ParseStatus;
  parse_confidence: number | null;
  needs_manual_review: boolean;
  missing_fields: string[];
  page_count: number;
  is_scanned: boolean;
  warning: string | null;
  error: string | null;
}

export interface CaseResponse {
  id: string;
  customer_name: string;
  consultant_id: string | null;
  status: CaseStatus;
  notes: string | null;
  clinical_summary: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  attachments: AttachmentResponse[];
}

export interface AttachmentFailure {
  code: string;
  message: string;
  retryable: boolean;
}

export interface AttachmentUploadItem {
  file_id: string | null;
  filename: string;
  attachment_type: AttachmentType;
  status: "parsed" | "pending" | "questionnaire_imported" | "duplicate" | "failed";
  media_type: string;
  size_bytes: number;
  parse_status: ParseStatus | null;
  lab_item_count: number;
  warnings: string[];
  failure: AttachmentFailure | null;
}

export interface AttachmentBatchResponse {
  items: AttachmentUploadItem[];
  meta: {
    case_id: string;
    case_status: CaseStatus;
    accepted_count: number;
    failed_count: number;
  };
}

export interface StartAnalysisRequest {
  third_party_processing_confirmed: boolean;
}

export interface OperationFailure {
  code: string;
  message: string;
  retryable: boolean;
}

export type OperationStatus = "queued" | "running" | "succeeded" | "failed";

export interface OperationResponse {
  operation_id: string;
  kind: "case_workflow";
  stage: "analysis" | "draft_generation";
  status: OperationStatus;
  case_id: string;
  analysis_id: string;
  draft_id: string | null;
  progress: {
    current: number;
    total: number;
    percent: number;
    current_item: string | null;
  };
  failure: OperationFailure | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface FindingResponse {
  id: string;
  name: string;
  result_text: string | null;
  raw_value: string | null;
  unit: string | null;
  reference_range: string | null;
  abnormal_flag: string;
  interpretation: string | null;
  report_explanation: string | null;
  neutral_interpretation: string | null;
  support_need_text: string | null;
  source_file_id: string;
  source_file_name: string;
  source_page: number;
  source_text: string;
  confidence: number;
  evidence_status: string;
  evidence_notes: string[];
  observed_at: IsoDateTime | null;
}

export interface SupplementResponse {
  id: string;
  name: string;
  source_file_ids: string[];
  source_file_names: string[];
  doctor_added: boolean;
}

export interface FoodSensitivityItemResponse {
  id: string;
  name: string;
  raw_value: string | null;
  unit: string | null;
  abnormal_flag: string;
  severity: FoodSensitivitySeverity;
  reported_grade: string | null;
  reported_grade_meaning: string | null;
  reference_range: string | null;
  grading_basis: string | null;
  source_page: number;
  source_text: string;
  evidence_status: string;
}

export interface FoodSensitivityResponse {
  source_file_id: string;
  source_file_name: string;
  source_page: number;
  items: FoodSensitivityItemResponse[];
  interpretations: string[];
  valid: boolean;
  warning: string | null;
}

export type AnalysisStatus =
  | "queued"
  | "preparing"
  | "analyzing_documents"
  | "synthesizing"
  | "validating"
  | "ready_for_review"
  | "reviewed"
  | "stale"
  | "failed";

export type DraftGenerationStatus =
  | "idle"
  | "queued"
  | "final_synthesizing"
  | "validating_support_needs"
  | "mapping_products"
  | "checking_safety"
  | "generating_draft"
  | "ready"
  | "failed";

export interface AnalysisResponse {
  id: string;
  case_id: string;
  version: number;
  revision: number;
  status: AnalysisStatus;
  progress: {
    current: number;
    total: number;
    percent: number;
    current_file_name: string | null;
  };
  case_summary: string | null;
  system_findings: string[];
  abnormal_findings: FindingResponse[];
  current_supplements: SupplementResponse[];
  food_sensitivity: FoodSensitivityResponse | null;
  warnings: string[];
  error: OperationFailure | null;
  draft_generation: {
    status: DraftGenerationStatus;
    progress: number;
    error: string | null;
  };
  draft_id: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface FindingEditableFields {
  name: string;
  result_text: string | null;
  raw_value: string | null;
  unit: string | null;
  reference_range: string | null;
  abnormal_flag: AbnormalFlag;
  source_file_id: string;
  source_file_name: string;
  source_page: number;
  source_text: string;
}

export type FindingChange =
  | { op: "add"; value: FindingEditableFields }
  | { op: "update"; id: string; changes: Partial<FindingEditableFields> }
  | { op: "remove"; id: string };

export type SupplementChange =
  | { op: "add"; value: { name: string } }
  | { op: "update"; id: string; changes: { name: string } }
  | { op: "remove"; id: string };

export interface FoodSensitivityEditableFields {
  name: string;
  raw_value: string | null;
  unit: string | null;
  abnormal_flag: AbnormalFlag;
  severity: FoodSensitivitySeverity;
  reported_grade: string | null;
  reported_grade_meaning: string | null;
  reference_range: string | null;
  grading_basis: string | null;
  source_page: number;
  source_text: string;
}

export interface FoodSensitivityAddValue extends FoodSensitivityEditableFields {
  source_file_id: string;
  source_file_name: string;
}

export type FoodSensitivityChange =
  | { op: "add"; value: FoodSensitivityAddValue }
  | { op: "update"; id: string; changes: Partial<FoodSensitivityEditableFields> }
  | { op: "remove"; id: string };

export interface ReviewSubmitRequest {
  reviewer_id: string;
  expected_revision: number;
  finding_changes: FindingChange[];
  supplement_changes: SupplementChange[];
  food_sensitivity_changes: FoodSensitivityChange[];
}

export interface DosageRegimenResponse {
  unit: string;
  single_dose_min: number | null;
  single_dose_max: number | null;
  daily_frequency_min: number | null;
  daily_frequency_max: number | null;
  weekly_frequency_min: number | null;
  weekly_frequency_max: number | null;
  timing: string[];
  interval_hours_min: number | null;
  interval_hours_max: number | null;
  daily_max: number | null;
  duration: string | null;
  maintenance: string | null;
}

export interface DosageOptionResponse {
  option_id: string;
  label: string;
  display_text: string;
  requires_review: boolean;
  regimen: DosageRegimenResponse;
}

export interface DraftRecommendationResponse {
  sku_id: string;
  display_name: string;
  dosage: string;
  dosage_option_id: string | null;
  dosage_option_label: string | null;
  dosage_match_reasons: string[];
  dosage_options: DosageOptionResponse[];
  dosage_regimen: DosageRegimenResponse | null;
  reason: string;
  evidence_ids: string[];
  evidence_details: string[];
  warnings: string[];
  current_supplement_overlap_notice: string | null;
}

export type DraftStatus = "pending_review" | "approved" | "abstained";

export interface DraftResponse {
  id: string;
  case_id: string;
  status: DraftStatus;
  revision: number;
  public_summary: string[];
  key_lab_highlights: string[];
  recommended_skus: DraftRecommendationResponse[];
  lifestyle_actions: string[];
  rationale: string[];
  evidence_details: string[];
  contraindications: string[];
  missing_info: string[];
  confidence: number;
  abstain_reason: string | null;
  manual_review_required: boolean;
  red_flags: string[];
  generated_at: IsoDateTime;
}

export interface DosageOverrideRequest {
  sku_id: string;
  option_id: string;
  note: string | null;
}

export interface ApprovalRequest {
  reviewer_id: string;
  publishable_summary: string | null;
  excluded_sku_ids: string[];
  dosage_overrides: DosageOverrideRequest[];
}

export interface ApprovalResponse {
  draft_id: string;
  status: DraftStatus;
  reviewer_id: string;
  publishable_report: string;
  approved_at: IsoDateTime;
  report_ready: boolean;
  report_url: string;
}

export interface ReportResponse {
  draft_id: string;
  status: "ready";
  filename: string;
  download_url: string;
  approved_at: IsoDateTime;
}
