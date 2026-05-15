export type CaseStatus =
  | "intake"
  | "files_received"
  | "parsing_completed"
  | "ready_for_recommendation"
  | "draft_generated"
  | "under_review"
  | "approved";

export type AnalysisMode = "local_grounded" | "llm_primary";
export type WorkspaceScope = "public" | "doctor";
export type RuleScope = "public" | "private";
export type DoctorRole = "admin" | "doctor";

export type FileParseStatus = "pending" | "parsed" | "reviewed" | "failed";

export interface ReferenceRange {
  lower?: number | null;
  upper?: number | null;
  raw?: string | null;
}

export interface SourceSpan {
  file_id?: string | null;
  file_name: string;
  page: number;
  line_number?: number | null;
  snippet: string;
}

export interface ExtractedLabItem {
  marker_code: string;
  marker_name: string;
  raw_name?: string | null;
  raw_value?: string | null;
  value?: number | null;
  unit?: string | null;
  normalized_value?: number | null;
  normalized_unit?: string | null;
  ref_range?: ReferenceRange;
  abnormal_flag: string;
  confidence: number;
  source_span: SourceSpan;
}

export interface CaseIndicator {
  indicator_name: string;
  result_text: string;
  status: "normal" | "attention" | "positive" | "info";
  category: string;
  source_span: SourceSpan;
}

export interface UploadedFile {
  id: string;
  case_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  storage_uri?: string | null;
  uploaded_at: string;
  raw_extracted_text?: string | null;
  corrected_text?: string | null;
  source_spans: SourceSpan[];
  parse_confidence?: number | null;
  parse_status: FileParseStatus;
  needs_manual_review: boolean;
  missing_fields: string[];
}

export interface Questionnaire {
  age?: number | null;
  sex: "female" | "male" | "other" | "unknown";
  chief_concerns: string[];
  symptoms: string[];
  known_conditions: string[];
  family_history: string[];
  medications: string[];
  allergies: string[];
  food_sensitivities: string[];
  pregnant_or_lactating?: boolean | null;
  diet_pattern?: string | null;
  work_pattern?: string | null;
  sitting_hours_per_day?: number | null;
  dining_out_frequency?: string | null;
  seafood_intake_ratio?: string | null;
  red_meat_intake_ratio?: string | null;
  supplement_use?: string | null;
  chemical_sensitivity?: string | null;
  sleep_hours?: number | null;
  sleep_quality?: string | null;
  exercise_frequency?: string | null;
  bowel_habits?: string | null;
  stress_level?: "low" | "medium" | "high" | null;
  emotional_state: string[];
  goals: string[];
  msq_system_scores: Record<string, number>;
  additional_notes?: string | null;
}

export interface CaseRecord {
  id: string;
  customer_name: string;
  consultant_id?: string | null;
  workspace_scope: WorkspaceScope;
  owner_doctor_id?: string | null;
  analysis_mode: AnalysisMode;
  status: CaseStatus;
  created_at: string;
  updated_at: string;
  notes?: string | null;
  clinical_summary_text?: string | null;
  files: UploadedFile[];
  questionnaire?: Questionnaire | null;
  extracted_lab_items: ExtractedLabItem[];
  draft_ids: string[];
  flags: string[];
  parsing_review_completed: boolean;
  parsing_reviewed_at?: string | null;
  parsing_reviewed_by?: string | null;
  parsing_missing_fields: string[];
  parsing_review_notes?: string | null;
}

export interface DraftRecommendationItem {
  sku_id: string;
  display_name: string;
  dosage: string;
  reason: string;
  evidence_ids: string[];
  evidence_details: string[];
  warnings: string[];
}

export interface RecommendationDraft {
  id: string;
  case_id: string;
  status: "pending_review" | "approved" | "abstained";
  case_summary: string[];
  key_lab_highlights: string[];
  recommended_skus: DraftRecommendationItem[];
  lifestyle_actions: string[];
  rationale: string[];
  evidence_ids: string[];
  evidence_details: string[];
  contraindications: string[];
  missing_info: string[];
  confidence: number;
  abstain_reason?: string | null;
  manual_review_required: boolean;
  red_flags: string[];
  report_sections: Record<string, string[] | string>;
  model_version: string;
  prompt_version: string;
  rule_version: string;
  generated_at: string;
}

export interface ReviewDecision {
  draft_id: string;
  reviewer_id: string;
  edits: Record<string, unknown>;
  final_status: string;
  publishable_report: string;
  pdf_report_path?: string | null;
  pdf_report_filename?: string | null;
  audit_log_id: string;
  approved_at: string;
}

export interface AuditLog {
  id: string;
  entity_type: string;
  entity_id: string;
  action: string;
  actor_id: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface CaseSummary {
  id: string;
  customer_name: string;
  analysis_mode: AnalysisMode;
  status: CaseStatus;
  consultant_id?: string | null;
  workspace_scope: WorkspaceScope;
  owner_doctor_id?: string | null;
  created_at: string;
  updated_at: string;
  file_count: number;
  lab_item_count: number;
  latest_draft_id?: string | null;
}

export interface CaseDetailResponse {
  case: CaseRecord;
  display_indicators: CaseIndicator[];
  latest_draft?: RecommendationDraft | null;
  review_decision?: ReviewDecision | null;
  audit_logs: AuditLog[];
  matched_clinician_rules: ClinicianRule[];
}

export interface ClinicalSummaryImageImportResult {
  case_detail: CaseDetailResponse;
  filename: string;
  extracted_text: string;
  confidence: number;
}

export interface ParsingReviewFileInput {
  file_id: string;
  corrected_text?: string | null;
  missing_fields: string[];
}

export interface ParsingReviewInput {
  reviewer_id: string;
  files: ParsingReviewFileInput[];
  normalized_lab_items: ExtractedLabItem[];
  missing_fields: string[];
  review_notes?: string | null;
}

export interface KnowledgeManifestEntry {
  entry_id: string;
  relative_path: string;
  source_type: string;
  topic: string;
  extract_status: string;
  review_status: string;
  tags: string[];
}

export interface ProductRule {
  sku_id: string;
  display_name: string;
  category: string;
  source_refs: string[];
  formula_summary: string;
  core_ingredients: string[];
  candidate_use_cases: string[];
  contraindications: string[];
  enabled: boolean;
  merge_status?: string | null;
  indications: string[];
  exclusions: string[];
  dosage_rule: string;
  interaction_rule: string[];
  warning_text: string[];
  lifestyle_tags: string[];
  priority: number;
}

export type ClinicianRuleAction = "boost" | "avoid";

export interface ClinicianRule {
  id: string;
  title: string;
  instruction_text: string;
  source_case_id?: string | null;
  created_by: string;
  scope: RuleScope;
  owner_doctor_id?: string | null;
  created_by_doctor_id?: string | null;
  created_at: string;
  updated_at: string;
  enabled: boolean;
  action: ClinicianRuleAction;
  strength: number;
  target_sku_ids: string[];
  trigger_marker_rules: string[];
  trigger_support_profiles: string[];
  trigger_goals: string[];
  trigger_symptoms: string[];
  trigger_chief_concerns: string[];
  trigger_conditions: string[];
  notes?: string | null;
}

export interface AssistantChatHistoryMessage {
  role: "assistant" | "doctor";
  text: string;
}

export interface AssistantCaseChatResponse {
  reply: string;
  mode: "llm" | "local";
  model_label: string;
}

export interface LLMConfig {
  base_url?: string | null;
  api_key?: string | null;
  model?: string | null;
  api_style: string;
  timeout_seconds: number;
  temperature: number;
  configured: boolean;
  validation_error?: string | null;
}

export interface DoctorAccount {
  id: string;
  username: string;
  display_name: string;
  role: DoctorRole;
  enabled: boolean;
}

export interface AuthResponse {
  doctor: DoctorAccount;
}

export interface AuthMeResponse {
  doctor?: DoctorAccount | null;
}
