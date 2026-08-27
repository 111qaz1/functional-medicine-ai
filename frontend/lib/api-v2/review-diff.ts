import type {
  AbnormalFlag,
  AnalysisResponse,
  FindingChange,
  FindingEditableFields,
  FoodSensitivityAddValue,
  FoodSensitivityChange,
  FoodSensitivityEditableFields,
  FoodSensitivitySeverity,
  SupplementChange
} from "./types";

const abnormalFlags = new Set<AbnormalFlag>(["low", "normal", "high", "positive", "unknown"]);
const severities = new Set<FoodSensitivitySeverity>(["mild", "moderate", "high", "ungraded"]);

export interface FindingDraft extends Omit<FindingEditableFields, "abnormal_flag"> {
  id: string;
  abnormal_flag: string;
  is_new: boolean;
}

export interface SupplementDraft {
  id: string;
  name: string;
  is_new: boolean;
}

export interface FoodSensitivityDraft extends Omit<FoodSensitivityEditableFields, "abnormal_flag" | "severity"> {
  id: string;
  abnormal_flag: string;
  severity: string;
  source_file_id: string;
  source_file_name: string;
  is_new: boolean;
}

export interface ReviewDraftState {
  findings: FindingDraft[];
  supplements: SupplementDraft[];
  foodSensitivityItems: FoodSensitivityDraft[];
}

export interface ReviewChanges {
  finding_changes: FindingChange[];
  supplement_changes: SupplementChange[];
  food_sensitivity_changes: FoodSensitivityChange[];
}

export function createReviewDraft(analysis: AnalysisResponse): ReviewDraftState {
  return {
    findings: analysis.abnormal_findings.map((item) => ({
      id: item.id,
      name: item.name,
      result_text: item.result_text,
      raw_value: item.raw_value,
      unit: item.unit,
      reference_range: item.reference_range,
      abnormal_flag: item.abnormal_flag,
      source_file_id: item.source_file_id,
      source_file_name: item.source_file_name,
      source_page: item.source_page,
      source_text: item.source_text,
      is_new: false
    })),
    supplements: analysis.current_supplements.map((item) => ({
      id: item.id,
      name: item.name,
      is_new: false
    })),
    foodSensitivityItems: (analysis.food_sensitivity?.items ?? []).map((item) => ({
      id: item.id,
      name: item.name,
      raw_value: item.raw_value,
      unit: item.unit,
      abnormal_flag: item.abnormal_flag,
      severity: item.severity,
      reported_grade: item.reported_grade,
      reported_grade_meaning: item.reported_grade_meaning,
      reference_range: item.reference_range,
      grading_basis: item.grading_basis,
      source_page: item.source_page,
      source_text: item.source_text,
      source_file_id: analysis.food_sensitivity?.source_file_id ?? "",
      source_file_name: analysis.food_sensitivity?.source_file_name ?? "",
      is_new: false
    }))
  };
}

function assertAbnormalFlag(value: string): AbnormalFlag {
  if (!abnormalFlags.has(value as AbnormalFlag)) {
    throw new Error("请选择接口允许的异常方向后再提交。");
  }
  return value as AbnormalFlag;
}

function assertSeverity(value: string): FoodSensitivitySeverity {
  if (!severities.has(value as FoodSensitivitySeverity)) {
    throw new Error("请选择接口允许的食敏严重程度后再提交。");
  }
  return value as FoodSensitivitySeverity;
}

function findingValue(draft: FindingDraft): FindingEditableFields {
  if (!draft.name.trim() || !draft.source_file_id.trim() || !draft.source_file_name.trim() || !draft.source_text.trim()) {
    throw new Error("异常指标的名称、来源文件和原文证据不能为空。");
  }
  return {
    name: draft.name.trim(),
    result_text: draft.result_text,
    raw_value: draft.raw_value,
    unit: draft.unit,
    reference_range: draft.reference_range,
    abnormal_flag: assertAbnormalFlag(draft.abnormal_flag),
    source_file_id: draft.source_file_id,
    source_file_name: draft.source_file_name,
    source_page: draft.source_page,
    source_text: draft.source_text.trim()
  };
}

function findingUpdate(baseline: FindingDraft, current: FindingDraft): Partial<FindingEditableFields> {
  const changes: Partial<FindingEditableFields> = {};
  const scalarKeys: Array<Exclude<keyof FindingEditableFields, "abnormal_flag">> = [
    "name",
    "result_text",
    "raw_value",
    "unit",
    "reference_range",
    "source_file_id",
    "source_file_name",
    "source_page",
    "source_text"
  ];
  for (const key of scalarKeys) {
    if (!Object.is(baseline[key], current[key])) {
      (changes as Record<string, unknown>)[key] = current[key];
    }
  }
  if (baseline.abnormal_flag !== current.abnormal_flag) {
    changes.abnormal_flag = assertAbnormalFlag(current.abnormal_flag);
  }
  if (
    (typeof changes.name === "string" && !changes.name.trim()) ||
    (typeof changes.source_file_id === "string" && !changes.source_file_id.trim()) ||
    (typeof changes.source_file_name === "string" && !changes.source_file_name.trim()) ||
    (typeof changes.source_text === "string" && !changes.source_text.trim())
  ) {
    throw new Error("异常指标的名称、来源文件和原文证据不能为空。");
  }
  return changes;
}

function foodValue(draft: FoodSensitivityDraft): FoodSensitivityAddValue {
  if (!draft.name.trim() || !draft.source_file_id.trim() || !draft.source_file_name.trim() || !draft.source_text.trim()) {
    throw new Error("食敏条目的名称、来源文件和原文证据不能为空。");
  }
  return {
    name: draft.name.trim(),
    raw_value: draft.raw_value,
    unit: draft.unit,
    abnormal_flag: assertAbnormalFlag(draft.abnormal_flag),
    severity: assertSeverity(draft.severity),
    reported_grade: draft.reported_grade,
    reported_grade_meaning: draft.reported_grade_meaning,
    reference_range: draft.reference_range,
    grading_basis: draft.grading_basis,
    source_file_id: draft.source_file_id,
    source_file_name: draft.source_file_name,
    source_page: draft.source_page,
    source_text: draft.source_text.trim()
  };
}

function foodUpdate(
  baseline: FoodSensitivityDraft,
  current: FoodSensitivityDraft
): Partial<FoodSensitivityEditableFields> {
  const changes: Partial<FoodSensitivityEditableFields> = {};
  const scalarKeys: Array<Exclude<keyof FoodSensitivityEditableFields, "abnormal_flag" | "severity">> = [
    "name",
    "raw_value",
    "unit",
    "reported_grade",
    "reported_grade_meaning",
    "reference_range",
    "grading_basis",
    "source_page",
    "source_text"
  ];
  for (const key of scalarKeys) {
    if (!Object.is(baseline[key], current[key])) {
      (changes as Record<string, unknown>)[key] = current[key];
    }
  }
  if (baseline.abnormal_flag !== current.abnormal_flag) {
    changes.abnormal_flag = assertAbnormalFlag(current.abnormal_flag);
  }
  if (baseline.severity !== current.severity) {
    changes.severity = assertSeverity(current.severity);
  }
  if (
    (typeof changes.name === "string" && !changes.name.trim()) ||
    (typeof changes.source_text === "string" && !changes.source_text.trim())
  ) {
    throw new Error("食敏条目的名称和原文证据不能为空。");
  }
  return changes;
}

export function buildReviewChanges(analysis: AnalysisResponse, draft: ReviewDraftState): ReviewChanges {
  const findingBaseline = new Map(createReviewDraft(analysis).findings.map((item) => [item.id, item]));
  const currentFindingIds = new Set(draft.findings.filter((item) => !item.is_new).map((item) => item.id));
  const findingChanges: FindingChange[] = [];
  for (const item of draft.findings) {
    if (item.is_new) {
      findingChanges.push({ op: "add", value: findingValue(item) });
      continue;
    }
    const baseline = findingBaseline.get(item.id);
    if (!baseline) throw new Error("复核列表包含不属于当前分析的异常指标。");
    const changes = findingUpdate(baseline, item);
    if (Object.keys(changes).length) findingChanges.push({ op: "update", id: item.id, changes });
  }
  for (const id of findingBaseline.keys()) {
    if (!currentFindingIds.has(id)) findingChanges.push({ op: "remove", id });
  }

  const supplementBaseline = new Map(createReviewDraft(analysis).supplements.map((item) => [item.id, item]));
  const currentSupplementIds = new Set(draft.supplements.filter((item) => !item.is_new).map((item) => item.id));
  const supplementChanges: SupplementChange[] = [];
  for (const item of draft.supplements) {
    const name = item.name.trim();
    if (!name) throw new Error("补充剂名称不能为空。");
    if (item.is_new) {
      supplementChanges.push({ op: "add", value: { name } });
    } else {
      const baseline = supplementBaseline.get(item.id);
      if (!baseline) throw new Error("复核列表包含不属于当前分析的补充剂。");
      if (baseline.name !== name) supplementChanges.push({ op: "update", id: item.id, changes: { name } });
    }
  }
  for (const id of supplementBaseline.keys()) {
    if (!currentSupplementIds.has(id)) supplementChanges.push({ op: "remove", id });
  }

  const foodBaseline = new Map(createReviewDraft(analysis).foodSensitivityItems.map((item) => [item.id, item]));
  const currentFoodIds = new Set(draft.foodSensitivityItems.filter((item) => !item.is_new).map((item) => item.id));
  const foodChanges: FoodSensitivityChange[] = [];
  for (const item of draft.foodSensitivityItems) {
    if (item.is_new) {
      foodChanges.push({ op: "add", value: foodValue(item) });
      continue;
    }
    const baseline = foodBaseline.get(item.id);
    if (!baseline) throw new Error("复核列表包含不属于当前分析的食敏条目。");
    const changes = foodUpdate(baseline, item);
    if (Object.keys(changes).length) foodChanges.push({ op: "update", id: item.id, changes });
  }
  for (const id of foodBaseline.keys()) {
    if (!currentFoodIds.has(id)) foodChanges.push({ op: "remove", id });
  }

  return {
    finding_changes: findingChanges,
    supplement_changes: supplementChanges,
    food_sensitivity_changes: foodChanges
  };
}
