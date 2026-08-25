"use client";

import React, { createContext, type FormEvent, type ReactNode, useContext, useEffect, useState } from "react";

import {
  fetchAuthBootstrap,
  fetchCurrentUser,
  loginDoctor,
  logoutDoctor,
  registerDoctor
} from "../../lib/api";
import type { DoctorAccount } from "../../lib/types";
import { WorkflowNotice } from "./workflow-shell";

interface DoctorSessionValue {
  doctor: DoctorAccount;
  logout(): Promise<void>;
}

const DoctorSessionContext = createContext<DoctorSessionValue | null>(null);

export function useIntegrationDoctor(): DoctorSessionValue {
  const value = useContext(DoctorSessionContext);
  if (!value) throw new Error("Integration doctor session is not available.");
  return value;
}

export function DoctorSessionGate({ children }: { children: ReactNode }) {
  const [doctor, setDoctor] = useState<DoctorAccount | null>(null);
  const [bootstrapRequired, setBootstrapRequired] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([fetchCurrentUser(), fetchAuthBootstrap()])
      .then(([session, bootstrap]) => {
        if (!active) return;
        setDoctor(session.doctor ?? null);
        setBootstrapRequired(bootstrap.required);
      })
      .catch(() => {
        if (active) setDoctor(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    function handleExpiredSession() {
      setDoctor(null);
      setError("登录会话已过期，请重新登录后继续。");
    }
    window.addEventListener("fm-session-expired", handleExpiredSession);
    return () => window.removeEventListener("fm-session-expired", handleExpiredSession);
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const username = String(form.get("username") ?? "").trim();
    const password = String(form.get("password") ?? "");
    const displayName = String(form.get("display_name") ?? "").trim();
    setSubmitting(true);
    setError(null);
    try {
      const response = bootstrapRequired
        ? await registerDoctor(username, password, displayName || undefined)
        : await loginDoctor(username, password);
      setDoctor(response.doctor);
      setBootstrapRequired(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "登录失败，请检查账号和密码。");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleLogout() {
    try {
      await logoutDoctor();
    } finally {
      setDoctor(null);
    }
  }

  if (loading) {
    return <main className="workflow-auth"><p>正在确认医生登录状态…</p></main>;
  }

  if (!doctor) {
    return (
      <main className="workflow-auth">
        <section className="workflow-auth__card" aria-labelledby="doctor-login-title">
          <p className="workflow-auth__eyebrow">Paracelsus AI</p>
          <h1 id="doctor-login-title">{bootstrapRequired ? "初始化系统管理员" : "医生登录"}</h1>
          <p>{bootstrapRequired ? "首次部署需要创建管理员，后续医生账号由管理员统一维护。" : "登录后进入本人病例列表和五步 AI 工作台。"}</p>
          {error ? <WorkflowNotice tone="error">{error}</WorkflowNotice> : null}
          <form className="workflow-form" onSubmit={(event) => void handleSubmit(event)}>
            <label className="workflow-field"><span>医生账号</span><input name="username" required autoComplete="username" /></label>
            {bootstrapRequired ? <label className="workflow-field"><span>医生姓名</span><input name="display_name" required autoComplete="name" /></label> : null}
            <label className="workflow-field"><span>密码</span><input name="password" type="password" required minLength={6} autoComplete={bootstrapRequired ? "new-password" : "current-password"} /></label>
            <button className="workflow-button workflow-button--primary" type="submit" disabled={submitting}>
              {submitting ? "正在验证…" : bootstrapRequired ? "创建管理员并进入" : "登录工作台"}
            </button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <DoctorSessionContext.Provider value={{ doctor, logout: handleLogout }}>
      {children}
    </DoctorSessionContext.Provider>
  );
}
