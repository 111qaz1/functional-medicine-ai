"use client";

import React, { type FormEvent, useMemo, useState } from "react";

import { workflowCopy } from "../../lib/api-v2/copy";
import { workflowErrorMessage } from "../../lib/api-v2/errors";
import { HttpWorkflowGateway } from "../../lib/api-v2/gateway";
import { WorkflowNotice, WorkflowSection, WorkflowShell } from "./workflow-shell";

const entrySteps = [
  { id: "case", state: "current" },
  { id: "attachments", state: "blocked" },
  { id: "analysis", state: "blocked" },
  { id: "review", state: "blocked" },
  { id: "draft", state: "blocked" },
  { id: "report", state: "blocked" }
] as const;

export function IntegrationEntry() {
  const gateway = useMemo(() => new HttpWorkflowGateway(), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    const form = new FormData(event.currentTarget);
    const caseId = String(form.get("case_id") ?? "").trim();
    if (!caseId) {
      setError("请输入病例 ID。");
      return;
    }
    window.location.assign(`/integration/cases/${encodeURIComponent(caseId)}`);
  }

  return (
    <WorkflowShell
      title={workflowCopy.entry.title}
      description={workflowCopy.entry.description}
      steps={[...entrySteps]}
      currentStep="case"
    >
      {error ? <WorkflowNotice tone="error">{error}</WorkflowNotice> : null}
      <div className="workflow-entry-grid">
        <WorkflowSection
          id="case"
          title={workflowCopy.entry.createTitle}
          description="创建操作只提交 v2 已声明的姓名、顾问 ID 和备注。"
          state="current"
        >
          <form className="workflow-form" onSubmit={(event) => void handleCreate(event)}>
            <label className="workflow-field">
              <span>客户名称</span>
              <input name="customer_name" required maxLength={160} autoComplete="off" />
            </label>
            <label className="workflow-field">
              <span>顾问 ID（可选）</span>
              <input name="consultant_id" maxLength={160} autoComplete="off" />
            </label>
            <label className="workflow-field">
              <span>备注（可选）</span>
              <textarea name="notes" rows={4} maxLength={4000} />
            </label>
            <button className="workflow-button workflow-button--primary" disabled={busy} type="submit">
              {busy ? "正在创建…" : workflowCopy.entry.createAction}
            </button>
          </form>
        </WorkflowSection>

        <section className="workflow-entry-resume" aria-labelledby="workflow-resume-title">
          <div className="workflow-section__header">
            <div>
              <h2 id="workflow-resume-title">{workflowCopy.entry.resumeTitle}</h2>
              <p>刷新页面后可通过病例 ID 恢复最新分析、草案和报告状态。</p>
            </div>
          </div>
          <div className="workflow-section__body">
            <form className="workflow-form" onSubmit={handleResume}>
              <label className="workflow-field">
                <span>病例 ID</span>
                <input name="case_id" required autoComplete="off" />
              </label>
              <button className="workflow-button workflow-button--secondary" type="submit">
                {workflowCopy.entry.resumeAction}
              </button>
            </form>
          </div>
        </section>
      </div>
    </WorkflowShell>
  );
}
