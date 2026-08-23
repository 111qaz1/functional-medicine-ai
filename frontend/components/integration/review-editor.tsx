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
  ["high", "高度"]
] as const;

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
  onSubmit(): void;
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
  onSubmit,
  onDiscardAndReload
}: ReviewEditorProps) {
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
        <div className="workflow-reviewer-identity"><span>当前复核医生</span><strong>{reviewerName}</strong><small>审核身份由登录会话确定，不能在请求中修改。</small></div>
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
            <h3>异常指标</h3>
            <p>仅可编辑 v2 公开字段，未修改的证据状态和置信度不会回传。</p>
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
        <div className="workflow-editor-list">
          {value.findings.map((item) => (
            <details className="workflow-editor-item" key={item.id} open={item.is_new}>
              <summary>
                <span>{item.name.trim() || "未命名异常指标"}</span>
                <small>{item.is_new ? "新增" : item.abnormal_flag}</small>
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
                  <label className="workflow-field">
                    <span>来源文件</span>
                    <select
                      value={item.source_file_id}
                      onChange={(event) => {
                        const source = sourceOptions.find((option) => option.id === event.target.value);
                        updateFinding(item.id, {
                          source_file_id: event.target.value,
                          source_file_name: source?.name ?? item.source_file_name
                        });
                      }}
                    >
                      {sourceOptions.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
                    </select>
                  </label>
                  <label className="workflow-field">
                    <span>页码</span>
                    <input type="number" min={1} value={item.source_page} onChange={(event) => updateFinding(item.id, { source_page: Number(event.target.value) || 1 })} />
                  </label>
                </div>
                <label className="workflow-field">
                  <span>原文证据</span>
                  <textarea rows={3} required value={item.source_text} onChange={(event) => updateFinding(item.id, { source_text: event.target.value })} />
                </label>
                <button
                  className="workflow-button workflow-button--danger"
                  type="button"
                  onClick={() => onChange({ ...value, findings: value.findings.filter((candidate) => candidate.id !== item.id) })}
                >
                  删除此指标
                </button>
              </div>
            </details>
          ))}
          {!value.findings.length ? <p className="workflow-empty">当前没有异常指标，可人工补充。</p> : null}
        </div>
      </div>

      <div className="workflow-editor-group">
        <div className="workflow-editor-group__header">
          <div>
            <h3>当前补充剂</h3>
            <p>医生可以更名、移除或新增患者当前使用的补充剂。</p>
          </div>
          <button className="workflow-button workflow-button--secondary" type="button" disabled={busy} onClick={addSupplement}>
            新增补充剂
          </button>
        </div>
        <div className="workflow-compact-list">
          {value.supplements.map((item) => (
            <div className="workflow-compact-row" key={item.id}>
              <label className="workflow-field">
                <span>补充剂名称</span>
                <input value={item.name} required onChange={(event) => updateSupplement(item.id, { name: event.target.value })} />
              </label>
              <button
                className="workflow-button workflow-button--danger"
                type="button"
                onClick={() => onChange({ ...value, supplements: value.supplements.filter((candidate) => candidate.id !== item.id) })}
              >
                移除
              </button>
            </div>
          ))}
          {!value.supplements.length ? <p className="workflow-empty">当前未记录补充剂。</p> : null}
        </div>
      </div>

      <div className="workflow-editor-group">
        <div className="workflow-editor-group__header">
          <div>
            <h3>慢性食物敏感</h3>
            <p>食敏修订仍绑定当前分析的来源文件，不接收内部映射字段。</p>
          </div>
          <button
            className="workflow-button workflow-button--secondary"
            type="button"
            disabled={busy || !(analysis.food_sensitivity || sourceOptions.length)}
            onClick={addFoodSensitivity}
          >
            新增食敏条目
          </button>
        </div>
        <div className="workflow-editor-list">
          {value.foodSensitivityItems.map((item) => (
            <details className="workflow-editor-item" key={item.id} open={item.is_new}>
              <summary>
                <span>{item.name.trim() || "未命名食敏条目"}</span>
                <small>{item.severity}</small>
              </summary>
              <div className="workflow-editor-item__body">
                <div className="workflow-form-grid">
                  <label className="workflow-field"><span>食物名称</span><input required value={item.name} onChange={(event) => updateFood(item.id, { name: event.target.value })} /></label>
                  <label className="workflow-field"><span>严重程度</span><select value={item.severity} onChange={(event) => updateFood(item.id, { severity: event.target.value })}>{foodSeverities.map(([severity, label]) => <option key={severity} value={severity}>{label}</option>)}</select></label>
                  <label className="workflow-field"><span>异常方向</span><select value={item.abnormal_flag} onChange={(event) => updateFood(item.id, { abnormal_flag: event.target.value })}>{editableAbnormalFlags.map(([flag, label]) => <option key={flag} value={flag}>{label}</option>)}</select></label>
                  <label className="workflow-field"><span>原始结果</span><input value={item.raw_value ?? ""} onChange={(event) => updateFood(item.id, { raw_value: event.target.value || null })} /></label>
                  <label className="workflow-field"><span>单位</span><input value={item.unit ?? ""} onChange={(event) => updateFood(item.id, { unit: event.target.value || null })} /></label>
                  <label className="workflow-field"><span>报告分级</span><input value={item.reported_grade ?? ""} onChange={(event) => updateFood(item.id, { reported_grade: event.target.value || null })} /></label>
                  <label className="workflow-field"><span>参考范围</span><input value={item.reference_range ?? ""} onChange={(event) => updateFood(item.id, { reference_range: event.target.value || null })} /></label>
                  <label className="workflow-field"><span>页码</span><input type="number" min={1} value={item.source_page} onChange={(event) => updateFood(item.id, { source_page: Number(event.target.value) || 1 })} /></label>
                </div>
                <label className="workflow-field"><span>原文证据</span><textarea rows={3} required value={item.source_text} onChange={(event) => updateFood(item.id, { source_text: event.target.value })} /></label>
                <button className="workflow-button workflow-button--danger" type="button" onClick={() => onChange({ ...value, foodSensitivityItems: value.foodSensitivityItems.filter((candidate) => candidate.id !== item.id) })}>删除此条目</button>
              </div>
            </details>
          ))}
          {!value.foodSensitivityItems.length ? <p className="workflow-empty">当前没有慢性食物敏感条目。</p> : null}
        </div>
      </div>

      <div className="workflow-action-row">
        <button
          className="workflow-button workflow-button--primary"
          type="button"
          disabled={busy || conflict}
          onClick={onSubmit}
        >
          {busy ? "正在提交…" : "确认复核并生成草案"}
        </button>
        <span>允许空差量，用于确认当前结果并继续。</span>
      </div>
    </div>
  );
}
