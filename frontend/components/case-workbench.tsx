"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useEffect, useState } from "react";

import { approveDraft, fetchCase, generateDraft, submitQuestionnaire, uploadCaseFile } from "../lib/api";
import { CaseDetailResponse, Questionnaire } from "../lib/types";
import { SectionCard } from "./section-card";
import { StatusPill } from "./status-pill";

function splitCsv(value: string) {
  return value
    .split(/[，,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function publicSafetyWarnings(warnings: string[]) {
  return Array.from(
    new Set(
      warnings
        .map((item) => item.trim().replaceAll("人工复核", "顾问确认"))
        .filter((item) => item && !item.toLowerCase().includes("sku") && !item.includes("规格"))
    )
  ).slice(0, 3);
}

const DEFAULT_FORM: Questionnaire = {
  sex: "unknown",
  chief_concerns: [],
  symptoms: [],
  known_conditions: [],
  family_history: [],
  medications: [],
  allergies: [],
  food_sensitivities: [],
  emotional_state: [],
  goals: [],
  msq_system_scores: {}
};

export function CaseWorkbench({ caseId }: { caseId: string }) {
  const [payload, setPayload] = useState<CaseDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [reviewerId, setReviewerId] = useState("reviewer-01");
  const [publishableSummary, setPublishableSummary] = useState("");
  const [questionnaire, setQuestionnaire] = useState<Questionnaire>(DEFAULT_FORM);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setLoading(true);
      const nextPayload = await fetchCase(caseId);
      setPayload(nextPayload);
      setQuestionnaire(nextPayload.case.questionnaire ?? DEFAULT_FORM);
      setPublishableSummary(nextPayload.review_decision?.publishable_report ?? "");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载病例失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [caseId]);

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    try {
      setBusy(true);
      const nextPayload = await uploadCaseFile(caseId, file);
      setPayload(nextPayload);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  async function handleQuestionnaireSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      setBusy(true);
      const nextPayload = await submitQuestionnaire(caseId, questionnaire);
      setPayload(nextPayload);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "问卷提交失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleGenerateDraft() {
    try {
      setBusy(true);
      await generateDraft(caseId, reviewerId);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成草案失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleApproveDraft() {
    if (!payload?.latest_draft) {
      return;
    }
    try {
      setBusy(true);
      await approveDraft(payload.latest_draft.id, reviewerId, publishableSummary || undefined);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "审核发布失败");
    } finally {
      setBusy(false);
    }
  }

  if (loading || !payload) {
    return <p className="muted">正在加载病例工作台...</p>;
  }

  const caseRecord = payload.case;
  const latestDraft = payload.latest_draft;

  return (
    <div className="workbench">
      <div className="workbench__hero">
        <div>
          <Link href="/" className="back-link">
            返回工作台
          </Link>
          <h1>{caseRecord.customer_name}</h1>
          <p className="muted">
            病例 ID {caseRecord.id} · 顾问 {caseRecord.consultant_id ?? "未分配"} · 最近更新时间{" "}
            {new Date(caseRecord.updated_at).toLocaleString("zh-CN")}
          </p>
        </div>
        <StatusPill status={caseRecord.status} />
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      <div className="workbench-grid">
        <SectionCard title="文件上传与解析" subtitle="Document intake">
          <label className="upload-dropzone">
            <input
              type="file"
              accept=".pdf,.doc,.docx,.txt,.png,.jpg,.jpeg,.pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
              onChange={handleUpload}
              disabled={busy}
            />
            <span>上传 PDF / DOCX / PNG / JPG / 文本样例</span>
            <small>Demo 版默认使用可替换的 OCR provider；生产环境可切到 Azure / Google / 私有 OCR。</small>
          </label>
          <div className="stack">
            {caseRecord.files.map((file) => (
              <div key={file.id} className="file-row">
                <div>
                  <strong>{file.filename}</strong>
                  <p className="muted">
                    {file.content_type} · {Math.round(file.size_bytes / 1024)} KB · 解析置信度{" "}
                    {file.parse_confidence ? Math.round(file.parse_confidence * 100) : 0}%
                  </p>
                </div>
              </div>
            ))}
            {caseRecord.files.length === 0 ? <p className="muted">还没有上传报告文件。</p> : null}
          </div>
        </SectionCard>

        <SectionCard title="问卷" subtitle="Questionnaire engine">
          <form className="stack" onSubmit={handleQuestionnaireSubmit}>
            <div className="grid-two">
              <label className="field">
                <span>年龄</span>
                <input
                  type="number"
                  value={questionnaire.age ?? ""}
                  onChange={(event) => setQuestionnaire((current) => ({ ...current, age: Number(event.target.value) || null }))}
                />
              </label>
              <label className="field">
                <span>性别</span>
                <select
                  value={questionnaire.sex}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, sex: event.target.value as Questionnaire["sex"] }))
                  }
                >
                  <option value="unknown">未填写</option>
                  <option value="female">女</option>
                  <option value="male">男</option>
                  <option value="other">其他</option>
                </select>
              </label>
            </div>

            <label className="field">
              <span>症状（逗号分隔）</span>
              <input
                value={questionnaire.symptoms.join("，")}
                onChange={(event) =>
                  setQuestionnaire((current) => ({ ...current, symptoms: splitCsv(event.target.value) }))
                }
                placeholder="疲劳，便秘，腹胀"
              />
            </label>

            <label className="field">
              <span>既往诊断</span>
              <input
                value={questionnaire.known_conditions.join("，")}
                onChange={(event) =>
                  setQuestionnaire((current) => ({ ...current, known_conditions: splitCsv(event.target.value) }))
                }
                placeholder="甲减，脂肪肝"
              />
            </label>

            <div className="grid-two">
              <label className="field">
                <span>用药</span>
                <input
                  value={questionnaire.medications.join("，")}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, medications: splitCsv(event.target.value) }))
                  }
                  placeholder="二甲双胍，华法林"
                />
              </label>
              <label className="field">
                <span>过敏</span>
                <input
                  value={questionnaire.allergies.join("，")}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, allergies: splitCsv(event.target.value) }))
                  }
                  placeholder="鱼，乳制品"
                />
              </label>
            </div>

            <div className="grid-two">
              <label className="field">
                <span>目标</span>
                <input
                  value={questionnaire.goals.join("，")}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, goals: splitCsv(event.target.value) }))
                  }
                  placeholder="血糖平衡，睡眠恢复，肠道支持"
                />
              </label>
              <label className="field">
                <span>压力等级</span>
                <select
                  value={questionnaire.stress_level ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({
                      ...current,
                      stress_level: (event.target.value || null) as Questionnaire["stress_level"]
                    }))
                  }
                >
                  <option value="">未填写</option>
                  <option value="low">低</option>
                  <option value="medium">中</option>
                  <option value="high">高</option>
                </select>
              </label>
            </div>

            <div className="grid-two">
              <label className="field">
                <span>睡眠时长</span>
                <input
                  type="number"
                  step="0.5"
                  value={questionnaire.sleep_hours ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, sleep_hours: Number(event.target.value) || null }))
                  }
                />
              </label>
              <label className="field">
                <span>睡眠质量</span>
                <input
                  value={questionnaire.sleep_quality ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, sleep_quality: event.target.value || null }))
                  }
                  placeholder="差 / 一般 / 好"
                />
              </label>
            </div>

            <div className="grid-two">
              <label className="field">
                <span>运动频率</span>
                <input
                  value={questionnaire.exercise_frequency ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, exercise_frequency: event.target.value || null }))
                  }
                  placeholder="每周 3 次 / 很少"
                />
              </label>
              <label className="field">
                <span>排便情况</span>
                <input
                  value={questionnaire.bowel_habits ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, bowel_habits: event.target.value || null }))
                  }
                  placeholder="便秘 / 正常 / 稀便"
                />
              </label>
            </div>

            <label className="field checkbox">
              <input
                type="checkbox"
                checked={Boolean(questionnaire.pregnant_or_lactating)}
                onChange={(event) =>
                  setQuestionnaire((current) => ({
                    ...current,
                    pregnant_or_lactating: event.target.checked
                  }))
                }
              />
              <span>妊娠或哺乳中</span>
            </label>

            <label className="field">
              <span>补充说明</span>
              <textarea
                rows={4}
                value={questionnaire.additional_notes ?? ""}
                onChange={(event) =>
                  setQuestionnaire((current) => ({ ...current, additional_notes: event.target.value || null }))
                }
                placeholder="饮食、家族史、目标、近期变化等"
              />
            </label>

            <button className="primary-button" disabled={busy}>
              保存问卷
            </button>
          </form>
        </SectionCard>

        <SectionCard title="解析指标" subtitle="Lab normalization">
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>指标</th>
                  <th>结果</th>
                  <th>状态</th>
                  <th>证据片段</th>
                </tr>
              </thead>
              <tbody>
                {caseRecord.extracted_lab_items.map((item) => (
                  <tr key={`${item.marker_code}-${item.source_span.line_number}`}>
                    <td>{item.marker_name}</td>
                    <td>
                      {item.normalized_value ?? item.value} {item.normalized_unit ?? item.unit}
                    </td>
                    <td>{item.abnormal_flag}</td>
                    <td>{item.source_span.snippet}</td>
                  </tr>
                ))}
                {caseRecord.extracted_lab_items.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="muted">
                      上传后会在这里显示标准化指标。
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </SectionCard>

        <SectionCard title="推荐草案与审核" subtitle="Grounded recommendation">
          <div className="stack">
            <label className="field">
              <span>审核人</span>
              <input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} />
            </label>
            <button className="primary-button" disabled={busy} onClick={() => void handleGenerateDraft()}>
              生成草案
            </button>
            {latestDraft ? (
              <>
                <div className="draft-meta">
                  <strong>草案状态：{latestDraft.status}</strong>
                  <span>置信度 {Math.round(latestDraft.confidence * 100)}%</span>
                </div>
                {latestDraft.abstain_reason ? <p className="warning-box">{latestDraft.abstain_reason}</p> : null}
                <div className="chip-list">
                  {latestDraft.red_flags.map((flag) => (
                    <span key={flag} className="chip chip--danger">
                      {flag}
                    </span>
                  ))}
                  {latestDraft.missing_info.map((item) => (
                    <span key={item} className="chip chip--muted">
                      {item}
                    </span>
                  ))}
                </div>

                <div className="stack">
                  {latestDraft.recommended_skus.map((sku) => (
                    <article key={sku.sku_id} className="recommendation-row">
                      <div>
                        <strong>{sku.display_name}</strong>
                        <p>{sku.reason}</p>
                        <p className="muted">
                          {sku.dosage} · 证据 {sku.evidence_ids.join("、") || "无"}
                        </p>
                        {publicSafetyWarnings(sku.warnings).length ? (
                          <p className="muted">注意/禁忌：{publicSafetyWarnings(sku.warnings).join("；")}</p>
                        ) : null}
                      </div>
                    </article>
                  ))}
                </div>

                <div className="grid-two">
                  <div>
                    <h3>推荐理由</h3>
                    <ul className="flat-list">
                      {latestDraft.rationale.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h3>生活方式建议</h3>
                    <ul className="flat-list">
                      {latestDraft.lifestyle_actions.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </div>
                </div>

                <label className="field">
                  <span>审核后发布文案</span>
                  <textarea
                    rows={8}
                    value={publishableSummary}
                    onChange={(event) => setPublishableSummary(event.target.value)}
                    placeholder="在这里编辑最终发给客户的版本"
                  />
                </label>

                <button className="primary-button" disabled={busy} onClick={() => void handleApproveDraft()}>
                  审核并发布
                </button>
              </>
            ) : (
              <p className="muted">问卷和文件就绪后可以生成草案。</p>
            )}
          </div>
        </SectionCard>

        <SectionCard title="审计日志" subtitle="Safety & audit">
          <div className="audit-list">
            {payload.audit_logs.map((item) => (
              <div key={item.id} className="audit-row">
                <strong>{item.action}</strong>
                <p className="muted">
                  {item.actor_id} · {new Date(item.created_at).toLocaleString("zh-CN")}
                </p>
              </div>
            ))}
          </div>
        </SectionCard>
      </div>
    </div>
  );
}
