"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { createCase, deleteCase, fetchCases } from "../lib/api";
import { AnalysisMode, CaseSummary, DoctorAccount, WorkspaceScope } from "../lib/types";
import { SectionCard } from "./section-card";
import { StatusPillLocal } from "./status-pill-local";

const ANALYSIS_MODE_STORAGE_KEY = "fm_last_analysis_mode";
const ANALYSIS_MODE_STORAGE_VERSION_KEY = "fm_analysis_mode_default_version";
const ANALYSIS_MODE_DEFAULT_VERSION = "llm-primary-default-v1";
const DEFAULT_ANALYSIS_MODE: AnalysisMode = "llm_primary";

function isAnalysisMode(value: string | null): value is AnalysisMode {
  return value === "local_grounded" || value === "llm_primary";
}

type DashboardLocalProps = {
  workspaceScope?: WorkspaceScope;
  currentDoctor?: DoctorAccount | null;
};

export function DashboardLocal({ workspaceScope = "public", currentDoctor = null }: DashboardLocalProps) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [customerName, setCustomerName] = useState("");
  const [consultantId, setConsultantId] = useState("nutrition-team");
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>(DEFAULT_ANALYSIS_MODE);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [deletingCaseId, setDeletingCaseId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setLoading(true);
      const nextCases = await fetchCases(workspaceScope);
      setCases(nextCases);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载病例失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [workspaceScope]);

  useEffect(() => {
    if (workspaceScope === "doctor" && currentDoctor) {
      setConsultantId(currentDoctor.display_name || currentDoctor.username);
    }
    if (workspaceScope === "public") {
      setConsultantId("public-workbench");
    }
  }, [currentDoctor, workspaceScope]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const savedVersion = window.localStorage.getItem(ANALYSIS_MODE_STORAGE_VERSION_KEY);
    const savedMode = window.localStorage.getItem(ANALYSIS_MODE_STORAGE_KEY);
    if (savedVersion === ANALYSIS_MODE_DEFAULT_VERSION && isAnalysisMode(savedMode)) {
      setAnalysisMode(savedMode);
      return;
    }

    setAnalysisMode(DEFAULT_ANALYSIS_MODE);
    window.localStorage.setItem(ANALYSIS_MODE_STORAGE_VERSION_KEY, ANALYSIS_MODE_DEFAULT_VERSION);
    window.localStorage.setItem(ANALYSIS_MODE_STORAGE_KEY, DEFAULT_ANALYSIS_MODE);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    window.localStorage.setItem(ANALYSIS_MODE_STORAGE_KEY, analysisMode);
  }, [analysisMode]);

  async function handleCreateCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!customerName.trim()) {
      return;
    }

    try {
      setSubmitting(true);
      const response = await createCase(
        customerName.trim(),
        consultantId.trim() || undefined,
        analysisMode,
        workspaceScope
      );
      setCustomerName("");
      setCases((current) => [
        {
          id: response.case.id,
          customer_name: response.case.customer_name,
          analysis_mode: response.case.analysis_mode,
          status: response.case.status,
          consultant_id: response.case.consultant_id,
          workspace_scope: response.case.workspace_scope,
          owner_doctor_id: response.case.owner_doctor_id,
          created_at: response.case.created_at,
          updated_at: response.case.updated_at,
          file_count: response.case.files.length,
          lab_item_count: response.case.extracted_lab_items.length,
          latest_draft_id: response.case.draft_ids.at(-1)
        },
        ...current
      ]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建病例失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDeleteCase(caseItem: CaseSummary) {
    if (typeof window !== "undefined") {
      const confirmed = window.confirm(
        `确认删除病例“${caseItem.customer_name}”吗？已上传文件、草案和 PDF 报告也会一起删除。`
      );
      if (!confirmed) {
        return;
      }
    }

    try {
      setDeletingCaseId(caseItem.id);
      await deleteCase(caseItem.id);
      setCases((current) => current.filter((item) => item.id !== caseItem.id));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除病例失败");
    } finally {
      setDeletingCaseId(null);
    }
  }

  const summary = {
    total: cases.length,
    approved: cases.filter((item) => item.status === "approved").length,
    inReview: cases.filter((item) => item.status === "under_review").length,
    intake: cases.filter((item) =>
      ["intake", "files_received", "parsing_completed", "ready_for_recommendation"].includes(item.status)
    ).length
  };

  return (
    <div className="dashboard-stack">
      <section className="dashboard-overview">
        <article className="overview-card">
          <span className="overview-card__label">病例总数</span>
          <strong>{summary.total}</strong>
          <p>当前工作台已经纳管的全部病例。</p>
        </article>
        <article className="overview-card">
          <span className="overview-card__label">待审核</span>
          <strong>{summary.inReview}</strong>
          <p>已经生成草案，等待顾问确认和发布。</p>
        </article>
        <article className="overview-card">
          <span className="overview-card__label">待补资料</span>
          <strong>{summary.intake}</strong>
          <p>还需要继续上传、校对或补问卷的病例。</p>
        </article>
        <article className="overview-card">
          <span className="overview-card__label">已发布</span>
          <strong>{summary.approved}</strong>
          <p>已经形成最终报告并支持导出 PDF。</p>
        </article>
      </section>

      <div className="dashboard-grid">
        <SectionCard title="快速建档" subtitle="Case intake">
          {workspaceScope === "doctor" && !currentDoctor ? (
            <p className="error-text">请先登录医生账号后再进入私人工作台。</p>
          ) : null}
          <form className="stack" onSubmit={handleCreateCase}>
            <label className="field">
              <span>客户姓名</span>
              <input
                value={customerName}
                onChange={(event) => setCustomerName(event.target.value)}
                placeholder="例如：张三"
              />
            </label>
            <label className="field">
              <span>{workspaceScope === "doctor" ? "当前医生" : "公共工作台标识"}</span>
              <input
                value={consultantId}
                onChange={(event) => setConsultantId(event.target.value)}
                placeholder={workspaceScope === "doctor" ? "医生姓名" : "public-workbench"}
              />
            </label>
            <label className="field">
              <span>分析模式</span>
              <select
                value={analysisMode}
                onChange={(event) => setAnalysisMode(event.target.value as AnalysisMode)}
              >
                <option value="local_grounded">本地知识优先</option>
                <option value="llm_primary">大模型优先，本地知识辅助</option>
              </select>
            </label>
            <button className="primary-button" disabled={submitting || (workspaceScope === "doctor" && !currentDoctor)}>
              {submitting ? "正在创建..." : "创建病例"}
            </button>
          </form>

          <div className="info-note">
            <strong>推荐流程</strong>
            <p>上传报告后先做人工校对，再生成草案、审核发布和导出 PDF，避免在未确认数据上直接给出结论。</p>
            <p>如果选择“大模型优先”，系统会保留当前报告结构，但改为由大模型主导分析，本地知识和产品目录作为辅助约束。</p>
            <p>使用“大模型优先”前，请先在“模型 API 配置”页完成可用模型设置；若未配置成功，系统会自动回退到本地知识优先流程。</p>
            <p>系统会记住你上一次选择的分析模式，下一次创建病例时默认沿用，不会自动切回本地知识优先。</p>
            <p>
              {workspaceScope === "doctor"
                ? "医生工作台里的病例只对当前登录医生可见。"
                : "公共工作台不需要登录，适合临时病例；这里不能保存新的医生规则。"}
            </p>
          </div>
        </SectionCard>

        <SectionCard title="病例队列" subtitle="Review queue">
          {error ? <p className="error-text">{error}</p> : null}
          {loading ? <p className="muted">正在加载病例...</p> : null}

          <div className="case-list">
            {cases.map((item) => (
              <article key={item.id} className="case-row">
                <Link href={`/cases/${item.id}`} className="case-row__link">
                  <div className="case-row__body">
                    <div className="case-row__title">
                      <strong>{item.customer_name}</strong>
                      <StatusPillLocal status={item.status} />
                    </div>
                    <p className="muted">
                      文件 {item.file_count} 份 · 指标 {item.lab_item_count} 项 · 顾问 {item.consultant_id ?? "未分配"}
                    </p>
                    <p className="muted">
                      模式：{item.analysis_mode === "llm_primary" ? "大模型优先" : "本地知识优先"}
                    </p>
                  </div>
                  <span className="case-row__arrow">继续处理</span>
                </Link>

                <div className="case-row__actions">
                  <button
                    type="button"
                    className="secondary-button secondary-button--danger"
                    disabled={deletingCaseId === item.id}
                    onClick={() => void handleDeleteCase(item)}
                  >
                    {deletingCaseId === item.id ? "删除中..." : "删除病例"}
                  </button>
                </div>
              </article>
            ))}

            {!loading && cases.length === 0 ? <p className="muted">还没有病例，可以先创建一个试试。</p> : null}
          </div>
        </SectionCard>
      </div>
    </div>
  );
}
