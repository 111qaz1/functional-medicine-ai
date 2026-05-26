"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { createCase, fetchCases } from "../lib/api";
import { CaseSummary } from "../lib/types";
import { SectionCard } from "./section-card";
import { StatusPill } from "./status-pill";

export function Dashboard() {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [customerName, setCustomerName] = useState("");
  const [consultantId, setConsultantId] = useState("nutrition-team");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setLoading(true);
      const nextCases = await fetchCases();
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
  }, []);

  async function handleCreateCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!customerName.trim()) {
      return;
    }

    try {
      setSubmitting(true);
      const response = await createCase(customerName.trim(), consultantId.trim() || undefined);
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
      setError(err instanceof Error ? err.message : "建档失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="dashboard-grid">
      <SectionCard title="病例入口" subtitle="Case intake">
        <form className="stack" onSubmit={handleCreateCase}>
          <label className="field">
            <span>客户姓名</span>
            <input value={customerName} onChange={(event) => setCustomerName(event.target.value)} placeholder="例如：张三" />
          </label>
          <label className="field">
            <span>顾问 ID</span>
            <input value={consultantId} onChange={(event) => setConsultantId(event.target.value)} placeholder="nutrition-team" />
          </label>
          <button className="primary-button" disabled={submitting}>
            {submitting ? "正在建档..." : "创建病例"}
          </button>
        </form>
        <p className="muted">
          这个控制台默认走“内部专家工具 + 人工审核”模式，所有自动草案都要先过审核台。
        </p>
      </SectionCard>

      <SectionCard title="病例列表" subtitle="Review queue">
        {error ? <p className="error-text">{error}</p> : null}
        {loading ? <p className="muted">正在加载病例...</p> : null}
        <div className="case-list">
          {cases.map((item) => (
            <Link key={item.id} href={`/cases/${item.id}`} className="case-row">
              <div>
                <div className="case-row__title">
                  <strong>{item.customer_name}</strong>
                  <StatusPill status={item.status} />
                </div>
                <p className="muted">
                  {item.file_count} 个文件 · {item.lab_item_count} 个指标 · 顾问 {item.consultant_id ?? "未分配"}
                </p>
              </div>
              <span className="case-row__arrow">查看</span>
            </Link>
          ))}
          {!loading && cases.length === 0 ? <p className="muted">还没有病例，先创建一个看看。</p> : null}
        </div>
      </SectionCard>
    </div>
  );
}
