"use client";

import React from "react";

import type { AnalysisResponse } from "../../lib/api-v2/types";
import type {
  FindingDraft,
  FoodSensitivityDraft,
  ReviewDraftState,
  SupplementDraft
} from "../../lib/api-v2/review-diff";
import { WorkflowNotice } from "./workflow-shell";

const editableAbnormalFlags = [
  ["unknown", "待确认"],
  ["low", "偏低"],
  ["normal", "正常"],
  ["high", "偏高"],
  ["positive", "阳性"]
] as const;

const foodSeverities = [
  ["ungraded", "未分级"],
  ["mild", "轻度"],
  ["moderate", "中度"],
  ["high", "重度"]
] as const;

const foodSensitivityGroups = [
  ["mild", "轻度"],
  ["moderate", "中度"],
  ["high", "重度"]
] as const;

type FindingFilter = "all" | "attention" | "needs_review" | "verified";

function abnormalFlagLabel(flag: string): string {
  if (flag === "high") return "偏高";
  if (flag === "low") return "偏低";
  if (flag === "positive") return "阳性/存在";
  if (flag === "normal") return "正常";
  if (flag === "genetic_risk") return "遗传风险";
  if (flag === "patient_reported") return "患者自述";
  return "待确认";
}

function findingResultLabel(item: FindingDraft, source: AnalysisResponse["abnormal_findings"][number] | undefined): string {
  const rawValue = item.raw_value?.trim() ?? "";
  const unit = item.unit?.trim() ?? "";
  if (rawValue) {
    return unit && !rawValue.toLowerCase().includes(unit.toLowerCase()) ? `${rawValue} ${unit}` : rawValue;
  }
  return item.result_text?.trim() || source?.interpretation?.trim() || "具体数值待确认";
}

function foodSensitivityResultLabel(item: FoodSensitivityDraft): string {
  const rawValue = item.raw_value?.trim() ?? "";
  const unit = item.unit?.trim() ?? "";
  if (!rawValue) return "结果待确认";
  return unit && !rawValue.toLowerCase().includes(unit.toLowerCase()) ? `${rawValue} ${unit}` : rawValue;
}

function supplementSourceLabel(
  item: SupplementDraft,
  source: AnalysisResponse["current_supplements"][number] | undefined
): string {
  if (item.is_new || source?.doctor_added) return "医生补充";
  const sourceNames = (source?.source_file_names ?? []).map((name) => name.trim()).filter(Boolean);
  return sourceNames.length ? `来源：${sourceNames.join("、")}` : "来源文件未记录";
}

function evidenceStatusLabel(status: string | undefined): string {
  if (status === "verified_text") return "文本证据已核对";
  if (status === "visual_model_only") return "扫描件：仅模型视觉识别";
  return "需医生确认";
}

function clientId(prefix: string): string {
  return `${prefix}_${crypto.randomUUID()}`;
}

export interface ReviewEditorProps {
  analysis: AnalysisResponse;
  value: ReviewDraftState;
  onChange(value: ReviewDraftState): void;
  reviewerName: string;
  sourceOptions: Array<{ id: string; name: string }>;
  busy: boolean;
  conflict: boolean;
  draftReady?: boolean;
  onSubmit(): void;
  onContinue?(): void;
  onDiscardAndReload(): void;
}

export function ReviewEditor({
  analysis,
  value,
  onChange,
  reviewerName,
  sourceOptions,
  busy,
  conflict,
  draftReady = false,
  onSubmit,
  onContinue,
  onDiscardAndReload
}: ReviewEditorProps) {
  const [findingFilter, setFindingFilter] = React.useState<FindingFilter>("all");
  const [findingSearch, setFindingSearch] = React.useState("");
  const sourceFindingById = new Map(analysis.abnormal_findings.map((item) => [item.id, item]));
  const sourceSupplementById = new Map(analysis.current_supplements.map((item) => [item.id, item]));
  const hasDetectedFoodSensitivity = Boolean(analysis.food_sensitivity?.items.length);
  const isAttentionFinding = (item: FindingDraft) => ["high", "low", "positive", "genetic_risk"].includes(item.abnormal_flag);
  const findingNeedsReview = (item: FindingDraft) => item.is_new || sourceFindingById.get(item.id)?.evidence_status === "needs_review";
  const findingIsVerified = (item: FindingDraft) => item.abnormal_flag === "patient_reported" || sourceFindingById.get(item.id)?.evidence_status === "verified_text";
  const normalizedSearch = findingSearch.trim().toLocaleLowerCase("zh-CN");
  const visibleFindings = value.findings.filter((item) => {
    const source = sourceFindingById.get(item.id);
    const matchesFilter = findingFilter === "all"
      || (findingFilter === "attention" && isAttentionFinding(item))
      || (findingFilter === "needs_review" && findingNeedsReview(item))
      || (findingFilter === "verified" && findingIsVerified(item));
    if (!matchesFilter || !normalizedSearch) return matchesFilter;
    return [
      item.name,
      item.result_text,
      item.raw_value,
      item.reference_range,
      item.source_text,
      source?.interpretation,
      source?.report_explanation,
      source?.neutral_interpretation
    ].some((candidate) => candidate?.toLocaleLowerCase("zh-CN").includes(normalizedSearch));
  });
  const updateFinding = (id: string, changes: Partial<FindingDraft>) => {
    onChange({
      ...value,
      findings: value.findings.map((item) => item.id === id ? { ...item, ...changes } : item)
    });
  };
  const updateSupplement = (id: string, changes: Partial<SupplementDraft>) => {
    onChange({
      ...value,
      supplements: value.supplements.map((item) => item.id === id ? { ...item, ...changes } : item)
    });
  };
  const updateFood = (id: string, changes: Partial<FoodSensitivityDraft>) => {
    onChange({
      ...value,
      foodSensitivityItems: value.foodSensitivityItems.map((item) => item.id === id ? { ...item, ...changes } : item)
    });
  };

  function addFinding() {
    const source = sourceOptions[0];
    if (!source) return;
    onChange({
      ...value,
      findings: [
        ...value.findings,
        {
          id: clientId("finding"),
          name: "",
          result_text: null,
          raw_value: null,
          unit: null,
          reference_range: null,
          abnormal_flag: "unknown",
          source_file_id: source.id,
          source_file_name: source.name,
          source_page: 1,
          source_text: "",
          is_new: true
        }
      ]
    });
  }

  function addSupplement() {
    onChange({
      ...value,
      supplements: [...value.supplements, { id: clientId("supplement"), name: "", is_new: true }]
    });
  }

  function addFoodSensitivity() {
    const source = analysis.food_sensitivity
      ? { id: analysis.food_sensitivity.source_file_id, name: analysis.food_sensitivity.source_file_name }
      : sourceOptions[0];
    if (!source) return;
    onChange({
      ...value,
      foodSensitivityItems: [
        ...value.foodSensitivityItems,
        {
          id: clientId("food"),
          name: "",
          raw_value: null,
          unit: null,
          abnormal_flag: "unknown",
          severity: "ungraded",
          reported_grade: null,
          reported_grade_meaning: null,
          reference_range: null,
          grading_basis: null,
          source_page: 1,
          source_text: "",
          source_file_id: source.id,
          source_file_name: source.name,
          is_new: true
        }
      ]
    });
  }

  return (
    <div className="workflow-stack">
      <div className="workflow-review-heading">
        <div className="workflow-reviewer-identity"><span>当前复核医生</span><strong>{reviewerName}</strong><small>复核记录将自动使用当前登录医生身份。</small></div>
        <p>当前分析修订号：{analysis.revision}</p>
      </div>

      {conflict ? (
        <WorkflowNotice tone="error">
          <p>分析结果已被其他操作更新。本地编辑尚未自动合并或丢弃。</p>
          <button className="workflow-button workflow-button--secondary" type="button" onClick={onDiscardAndReload}>
            放弃本地编辑并重新获取
          </button>
        </WorkflowNotice>
      ) : null}

      <div className="workflow-editor-group">
        <div className="workflow-editor-group__header">
          <div>
            <h3>异常指标 <span className="workflow-count">{value.findings.length} 项</span></h3>
            <p>核对指标结果、参考范围和来源证据；未修改内容将保持原分析结果。</p>
          </div>
          <button
            className="workflow-button workflow-button--secondary"
            type="button"
            disabled={busy || !sourceOptions.length}
            onClick={addFinding}
          >
            新增异常指标
          </button>
        </div>
        <div className="workflow-finding-toolbar">
          <div className="workflow-finding-filters" role="group" aria-label="异常指标筛选">
            {([
              ["all", `全部 ${value.findings.length}`],
              ["attention", `异常 ${value.findings.filter(isAttentionFinding).length}`],
              ["needs_review", `待确认 ${value.findings.filter(findingNeedsReview).length}`],
              ["verified", `已核对 ${value.findings.filter(findingIsVerified).length}`]
            ] as Array<[FindingFilter, string]>).map(([filter, label]) => (
              <button className="workflow-finding-filter" data-active={findingFilter === filter} key={filter} type="button" onClick={() => setFindingFilter(filter)}>{label}</button>
            ))}
          </div>
          <input className="workflow-finding-search" type="search" value={findingSearch} onChange={(event) => setFindingSearch(event.target.value)} placeholder="搜索指标、结果或证据" aria-label="搜索异常指标" />
        </div>
        <div className="workflow-finding-grid">
          {visibleFindings.map((item) => {
            const source = sourceFindingById.get(item.id);
            return (
            <details className="workflow-finding-card" key={item.id} open={item.is_new || undefined}>
              <summary>
                <div className="workflow-finding-card__summary">
                  <div>
                    <strong>{item.name.trim() || "未命名异常指标"}</strong>
                    <p>{findingResultLabel(item, source)}</p>
                    {item.reference_range?.trim() ? <small>参考范围：{item.reference_range.trim()}</small> : null}
                  </div>
                  <span data-flag={item.abnormal_flag}>{item.is_new ? "新增" : abnormalFlagLabel(item.abnormal_flag)}</span>
                </div>
                <span className="workflow-finding-card__action">展开校对</span>
              </summary>
              <div className="workflow-editor-item__body">
                <div className="workflow-form-grid">
                  <label className="workflow-field">
                    <span>指标名称</span>
                    <input value={item.name} required onChange={(event) => updateFinding(item.id, { name: event.target.value })} />
                  </label>
                  <label className="workflow-field">
                    <span>异常方向</span>
                    <select value={item.abnormal_flag} onChange={(event) => updateFinding(item.id, { abnormal_flag: event.target.value })}>
                      {!editableAbnormalFlags.some(([flag]) => flag === item.abnormal_flag) ? (
                        <option value={item.abnormal_flag}>保留原值：{item.abnormal_flag}</option>
                      ) : null}
                      {editableAbnormalFlags.map(([flag, label]) => <option key={flag} value={flag}>{label}</option>)}
                    </select>
                  </label>
                  <label className="workflow-field">
                    <span>结果文本</span>
                    <input value={item.result_text ?? ""} onChange={(event) => updateFinding(item.id, { result_text: event.target.value || null })} />
                  </label>
                  <label className="workflow-field">
                    <span>原始数值</span>
                    <input value={item.raw_value ?? ""} onChange={(event) => updateFinding(item.id, { raw_value: event.target.value || null })} />
                  </label>
                  <label className="workflow-field">
                    <span>单位</span>
                    <input value={item.unit ?? ""} onChange={(event) => updateFinding(item.id, { unit: event.target.value || null })} />
                  </label>
                  <label className="workflow-field">
                    <span>参考范围</span>
                    <input value={item.reference_range ?? ""} onChange={(event) => updateFinding(item.id, { reference_range: event.target.value || null })} />
                  </label>
                </div>
                <details className="workflow-evidence-panel">
                  <summary>查看来源证据</summary>
                  <div className="workflow-evidence-panel__body">
                    <div className="workflow-form-grid">
                      <label className="workflow-field">
                        <span>来源文件</span>
                        <select value={item.source_file_id} onChange={(event) => {
                          const selectedSource = sourceOptions.find((option) => option.id === event.target.value);
                          updateFinding(item.id, { source_file_id: event.target.value, source_file_name: selectedSource?.name ?? item.source_file_name });
                        }}>
                          {sourceOptions.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
                        </select>
                      </label>
                      <label className="workflow-field"><span>页码</span><input type="number" min={1} value={item.source_page} onChange={(event) => updateFinding(item.id, { source_page: Number(event.target.value) || 1 })} /></label>
                    </div>
                    <label className="workflow-field"><span>原文证据</span><textarea rows={3} required value={item.source_text} onChange={(event) => updateFinding(item.id, { source_text: event.target.value })} /></label>
                    {source?.report_explanation ? <p className="workflow-readonly-explanation"><strong>报告解释：</strong>{source.report_explanation}</p> : null}
                    {source?.neutral_interpretation ? <p className="workflow-readonly-explanation"><strong>中性医学解释：</strong>{source.neutral_interpretation}</p> : null}
                    <p className="workflow-evidence-status">{evidenceStatusLabel(source?.evidence_status)}</p>
                  </div>
                </details>
                <button
                  className="workflow-button workflow-button--danger"
                  type="button"
                  onClick={() => onChange({ ...value, findings: value.findings.filter((candidate) => candidate.id !== item.id) })}
                >
                  删除此指标
                </button>
              </div>
            </details>
            );
          })}
          {!value.findings.length ? <p className="workflow-empty">当前没有异常指标，可人工补充。</p> : null}
          {value.findings.length > 0 && !visibleFindings.length ? <p className="workflow-empty">当前筛选条件下没有匹配的异常指标。</p> : null}
        </div>
      </div>

      <div className="workflow-editor-group">
        <div className="workflow-editor-group__header">
          <div>
            <h3>当前补充剂 <span className="workflow-count">{value.supplements.length} 项</span></h3>
            <p>医生可以更名、移除或新增患者当前使用的补充剂。</p>
          </div>
          <button className="workflow-button workflow-button--secondary" type="button" disabled={busy} onClick={addSupplement}>
            新增补充剂
          </button>
        </div>
        <div className="workflow-compact-list">
          {value.supplements.map((item) => {
            const source = sourceSupplementById.get(item.id);
            return (
            <div className="workflow-compact-row" key={item.id}>
              <label className="workflow-field">
                <span>补充剂名称</span>
                <input value={item.name} required onChange={(event) => updateSupplement(item.id, { name: event.target.value })} />
                <small className="workflow-supplement-source">{supplementSourceLabel(item, source)}</small>
              </label>
              <button
                className="workflow-button workflow-button--danger"
                type="button"
                onClick={() => onChange({ ...value, supplements: value.supplements.filter((candidate) => candidate.id !== item.id) })}
              >
                移除
              </button>
            </div>
            );
          })}
          {!value.supplements.length ? <p className="workflow-empty">当前未记录补充剂。</p> : null}
        </div>
      </div>

      {hasDetectedFoodSensitivity ? (
      <div className="workflow-editor-group">
        <div className="workflow-editor-group__header">
          <div>
            <h3>慢性食物敏感 <span className="workflow-count">{value.foodSensitivityItems.length} 项</span></h3>
            <p>按原报告结果分级核对，食敏项目不会混入普通异常指标。</p>
          </div>
        </div>
        <div className="workflow-food-groups">
          {foodSensitivityGroups.map(([severity, label]) => {
            const items = value.foodSensitivityItems.filter((item) => item.severity === severity);
            return (
              <section className="workflow-food-group" data-severity={severity} key={severity}>
                <header><h4>{label}</h4><span>{items.length} 项</span></header>
                <div className="workflow-food-group__items">
                  {items.length ? items.map((item) => (
                    <details className="workflow-food-item" key={item.id} open={item.is_new || undefined}>
                      <summary>
                        <span><strong>{item.name.trim() || "未命名食敏条目"}</strong><small>{foodSensitivityResultLabel(item)}</small></span>
                        <span className="workflow-food-item__action">展开校对</span>
                      </summary>
                      <div className="workflow-food-item__body">
                        <div className="workflow-form-grid">
                          <label className="workflow-field"><span>食物名称</span><input required value={item.name} onChange={(event) => updateFood(item.id, { name: event.target.value })} /></label>
                          <label className="workflow-field"><span>严重程度</span><select value={item.severity} onChange={(event) => updateFood(item.id, { severity: event.target.value })}>{foodSeverities.map(([option, optionLabel]) => <option key={option} value={option}>{optionLabel}</option>)}</select></label>
                          <label className="workflow-field"><span>检测结果</span><input value={item.raw_value ?? ""} onChange={(event) => updateFood(item.id, { raw_value: event.target.value || null })} /></label>
                          <label className="workflow-field"><span>单位</span><input value={item.unit ?? ""} onChange={(event) => updateFood(item.id, { unit: event.target.value || null })} /></label>
                          <label className="workflow-field"><span>原报告等级</span><input value={item.reported_grade ?? ""} onChange={(event) => updateFood(item.id, { reported_grade: event.target.value || null })} /></label>
                          <label className="workflow-field"><span>原等级含义</span><input value={item.reported_grade_meaning ?? ""} onChange={(event) => updateFood(item.id, { reported_grade_meaning: event.target.value || null })} /></label>
                          <label className="workflow-field"><span>报告状态</span><select value={item.abnormal_flag} onChange={(event) => updateFood(item.id, { abnormal_flag: event.target.value })}>{editableAbnormalFlags.map(([flag, flagLabel]) => <option key={flag} value={flag}>{flagLabel}</option>)}</select></label>
                          <label className="workflow-field"><span>来源页码</span><input type="number" min={1} value={item.source_page} onChange={(event) => updateFood(item.id, { source_page: Number(event.target.value) || 1 })} /></label>
                          <label className="workflow-field"><span>参考/分级范围</span><input value={item.reference_range ?? ""} onChange={(event) => updateFood(item.id, { reference_range: event.target.value || null })} /></label>
                          <label className="workflow-field"><span>分级依据</span><input value={item.grading_basis ?? ""} onChange={(event) => updateFood(item.id, { grading_basis: event.target.value || null })} /></label>
                        </div>
                        <label className="workflow-field"><span>原文证据</span><textarea rows={3} required value={item.source_text} onChange={(event) => updateFood(item.id, { source_text: event.target.value })} /></label>
                        <button className="workflow-button workflow-button--danger" type="button" onClick={() => onChange({ ...value, foodSensitivityItems: value.foodSensitivityItems.filter((candidate) => candidate.id !== item.id) })}>删除此条目</button>
                      </div>
                    </details>
                  )) : <p>暂无项目</p>}
                </div>
              </section>
            );
          })}
        </div>
        {value.foodSensitivityItems.some((item) => item.severity === "ungraded") ? (
          <section className="workflow-food-pending">
            <header>
              <div><h4>待确认</h4><p>原报告缺少可安全核对的等级对应关系，请展开后由医生确认。</p></div>
              <span>{value.foodSensitivityItems.filter((item) => item.severity === "ungraded").length} 项</span>
            </header>
            <div className="workflow-food-group__items">
              {value.foodSensitivityItems.filter((item) => item.severity === "ungraded").map((item) => (
                <details className="workflow-food-item" key={item.id} open={item.is_new || undefined}>
                  <summary>
                    <span><strong>{item.name.trim() || "未命名食敏条目"}</strong><small>{foodSensitivityResultLabel(item)}</small></span>
                    <span className="workflow-food-item__action">展开校对</span>
                  </summary>
                  <div className="workflow-food-item__body">
                    <div className="workflow-form-grid">
                      <label className="workflow-field"><span>食物名称</span><input required value={item.name} onChange={(event) => updateFood(item.id, { name: event.target.value })} /></label>
                      <label className="workflow-field"><span>严重程度</span><select value={item.severity} onChange={(event) => updateFood(item.id, { severity: event.target.value })}>{foodSeverities.map(([option, optionLabel]) => <option key={option} value={option}>{optionLabel}</option>)}</select></label>
                      <label className="workflow-field"><span>检测结果</span><input value={item.raw_value ?? ""} onChange={(event) => updateFood(item.id, { raw_value: event.target.value || null })} /></label>
                      <label className="workflow-field"><span>单位</span><input value={item.unit ?? ""} onChange={(event) => updateFood(item.id, { unit: event.target.value || null })} /></label>
                      <label className="workflow-field"><span>原报告等级</span><input value={item.reported_grade ?? ""} onChange={(event) => updateFood(item.id, { reported_grade: event.target.value || null })} /></label>
                      <label className="workflow-field"><span>原等级含义</span><input value={item.reported_grade_meaning ?? ""} onChange={(event) => updateFood(item.id, { reported_grade_meaning: event.target.value || null })} /></label>
                      <label className="workflow-field"><span>报告状态</span><select value={item.abnormal_flag} onChange={(event) => updateFood(item.id, { abnormal_flag: event.target.value })}>{editableAbnormalFlags.map(([flag, flagLabel]) => <option key={flag} value={flag}>{flagLabel}</option>)}</select></label>
                      <label className="workflow-field"><span>来源页码</span><input type="number" min={1} value={item.source_page} onChange={(event) => updateFood(item.id, { source_page: Number(event.target.value) || 1 })} /></label>
                      <label className="workflow-field"><span>参考/分级范围</span><input value={item.reference_range ?? ""} onChange={(event) => updateFood(item.id, { reference_range: event.target.value || null })} /></label>
                      <label className="workflow-field"><span>分级依据</span><input value={item.grading_basis ?? ""} onChange={(event) => updateFood(item.id, { grading_basis: event.target.value || null })} /></label>
                    </div>
                    <label className="workflow-field"><span>原文证据</span><textarea rows={3} required value={item.source_text} onChange={(event) => updateFood(item.id, { source_text: event.target.value })} /></label>
                    <button className="workflow-button workflow-button--danger" type="button" onClick={() => onChange({ ...value, foodSensitivityItems: value.foodSensitivityItems.filter((candidate) => candidate.id !== item.id) })}>删除此条目</button>
                  </div>
                </details>
              ))}
            </div>
          </section>
        ) : null}
        {analysis.food_sensitivity?.warning ? <WorkflowNotice tone="warning">{analysis.food_sensitivity.warning}</WorkflowNotice> : null}
        <button className="workflow-button workflow-button--secondary workflow-add-food" type="button" disabled={busy} onClick={addFoodSensitivity}>新增食敏条目</button>
      </div>
      ) : null}

      <div className="workflow-action-row workflow-action-dock">
        {draftReady ? (
          <>
            <WorkflowNotice tone="success">医生校对已保存，营养素草案已经生成。</WorkflowNotice>
            <button className="workflow-button workflow-button--primary" type="button" onClick={onContinue}>进入方案审核</button>
          </>
        ) : (
          <button
            className="workflow-button workflow-button--primary"
            type="button"
            disabled={busy || conflict}
            aria-busy={busy}
            onClick={onSubmit}
          >
            {busy ? "正在保存校对并生成营养素草案…" : "保存校对并生成营养素草案"}
          </button>
        )}
      </div>
    </div>
  );
}
