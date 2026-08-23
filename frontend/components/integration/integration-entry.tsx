"use client";

import React, { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ArrowPathIcon, ArrowRightStartOnRectangleIcon, PlusIcon, UserGroupIcon } from "@heroicons/react/24/outline";

import { caseStatusLabels, workflowCopy } from "../../lib/api-v2/copy";
import { workflowErrorMessage } from "../../lib/api-v2/errors";
import type { FixtureScenario } from "../../lib/api-v2/fixture-gateway";
import { createWorkflowGateway } from "../../lib/api-v2/gateway-factory";
import type { CaseSummaryResponse } from "../../lib/api-v2/types";
import { useIntegrationDoctor } from "./doctor-session";
import { WorkflowNotice, WorkflowSection, WorkflowShell } from "./workflow-shell";

const entrySteps = [
  { id: "case", state: "current" },
  { id: "attachments", state: "blocked" },
  { id: "analysis", state: "blocked" },
  { id: "review", state: "blocked" },
  { id: "draft", state: "blocked" },
  { id: "report", state: "blocked" }
] as const;

export function IntegrationEntry({ fixtureMode, fixtureScenario }: { fixtureMode: boolean; fixtureScenario: FixtureScenario }) {
  const gateway = useMemo(() => createWorkflowGateway(fixtureMode, fixtureScenario), [fixtureMode, fixtureScenario]);
  const { doctor, logout } = useIntegrationDoctor();
  const [cases, setCases] = useState<CaseSummaryResponse[]>([]);
  const [loadingCases, setLoadingCases] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadCases = useCallback(async () => {
    setLoadingCases(true);
    setError(null);
    try {
      const result = await gateway.listCases(0, 50);
      setCases(result.items);
    } catch (cause) {
      setError(workflowErrorMessage(cause));
    } finally {
      setLoadingCases(false);
    }
  }, [gateway]);

  useEffect(() => { void loadCases(); }, [loadCases]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const created = await gateway.createCase({
        customer_name: String(form.get("customer_name") ?? "").trim(),
        consultant_id: String(form.get("consultant_id") ?? "").trim() || null,
        notes: String(form.get("notes") ?? "").trim() || null
      });
      window.location.assign(`/integration/cases/${encodeURIComponent(created.id)}`);
    } catch (cause) {
      setError(workflowErrorMessage(cause));
      setBusy(false);
    }
  }

  function handleResume(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const caseId = String(new FormData(event.currentTarget).get("case_id") ?? "").trim();
    if (!caseId) {
      setError("请输入病例 ID。");
      return;
    }
    window.location.assign(`/integration/cases/${encodeURIComponent(caseId)}`);
  }

  return (
    <WorkflowShell
      title="我的 AI 病例"
      description={`当前医生：${doctor.display_name}。病例仅对本人可见。`}
      steps={[...entrySteps]}
      currentStep="case"
      theme={fixtureMode ? "test" : "paracelsus"}
      headerActions={
        <>
          {doctor.role === "admin" ? <a className="workflow-button workflow-button--secondary" href="/doctors"><UserGroupIcon className="workflow-button__icon" />账号管理</a> : null}
          <button className="workflow-button workflow-button--secondary" type="button" onClick={() => void logout()}><ArrowRightStartOnRectangleIcon className="workflow-button__icon" />退出登录</button>
        </>
      }
    >
      {fixtureMode ? <WorkflowNotice tone="warning">Fixture 模式已启用，场景：{fixtureScenario}。数据只用于界面测试。</WorkflowNotice> : null}
      {error ? <WorkflowNotice tone="error">{error}</WorkflowNotice> : null}

      <WorkflowSection id="case" title="我的病例" description="按最近更新时间排列，可继续尚未完成的六步流程。" state="current" actions={<button className="workflow-button workflow-button--secondary" type="button" disabled={loadingCases} onClick={() => void loadCases()}><ArrowPathIcon className={`workflow-button__icon${loadingCases ? " workflow-icon--spin" : ""}`} />刷新列表</button>}>
        {loadingCases ? <p className="workflow-placeholder">正在读取病例…</p> : cases.length ? (
          <div className="workflow-case-table-wrap">
            <table className="workflow-case-table">
              <thead><tr><th>客户</th><th>状态</th><th>附件</th><th>最近更新</th><th><span className="workflow-visually-hidden">操作</span></th></tr></thead>
              <tbody>
                {cases.map((item) => (
                  <tr key={item.id}>
                    <td><strong>{item.customer_name}</strong><small>{item.id}</small></td>
                    <td><span className="workflow-status-badge">{caseStatusLabels[item.status]}</span></td>
                    <td>{item.attachment_count}</td>
                    <td>{new Date(item.updated_at).toLocaleString("zh-CN")}</td>
                    <td><a className="workflow-button workflow-button--secondary" href={`/integration/cases/${encodeURIComponent(item.id)}`}>继续处理</a></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="workflow-empty">当前账号还没有病例，请创建第一份病例。</p>}
      </WorkflowSection>

      <div className="workflow-entry-grid">
        <section className="workflow-entry-create" aria-labelledby="workflow-create-title">
          <div className="workflow-section__header">
            <div>
              <h2 id="workflow-create-title">{workflowCopy.entry.createTitle}</h2>
              <p>创建后进入资料上传和分析流程。</p>
            </div>
          </div>
          <div className="workflow-section__body">
          <form className="workflow-form" onSubmit={(event) => void handleCreate(event)}>
            <label className="workflow-field"><span>客户名称</span><input name="customer_name" required maxLength={160} autoComplete="off" /></label>
            <label className="workflow-field"><span>顾问 ID（可选）</span><input name="consultant_id" maxLength={160} autoComplete="off" /></label>
            <label className="workflow-field"><span>备注（可选）</span><textarea name="notes" rows={4} maxLength={4000} /></label>
            <button className="workflow-button workflow-button--primary" disabled={busy} type="submit"><PlusIcon className="workflow-button__icon" />{busy ? "正在创建…" : workflowCopy.entry.createAction}</button>
          </form>
          </div>
        </section>

        <section className="workflow-entry-resume" aria-labelledby="workflow-resume-title">
          <div className="workflow-section__header"><div><h2 id="workflow-resume-title">按 ID 恢复</h2><p>仅用于联调和辅助排查，日常请从本人病例列表进入。</p></div></div>
          <div className="workflow-section__body">
            <form className="workflow-form" onSubmit={handleResume}>
              <label className="workflow-field"><span>病例 ID</span><input name="case_id" required autoComplete="off" /></label>
              <button className="workflow-button workflow-button--secondary" type="submit">{workflowCopy.entry.resumeAction}</button>
            </form>
          </div>
        </section>
      </div>
    </WorkflowShell>
  );
}
