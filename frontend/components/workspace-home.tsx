"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { fetchCurrentUser, loginDoctor, logoutDoctor, registerDoctor } from "../lib/api";
import { DoctorAccount, WorkspaceScope } from "../lib/types";
import { DashboardLocal } from "./dashboard-local";

type AuthMode = "login" | "register";

export function WorkspaceHome() {
  const [doctor, setDoctor] = useState<DoctorAccount | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceScope | null>(null);
  const [authMode, setAuthMode] = useState<AuthMode>("login");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [submittingAuth, setSubmittingAuth] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    async function loadCurrentUser() {
      try {
        const response = await fetchCurrentUser();
        setDoctor(response.doctor ?? null);
      } catch {
        setDoctor(null);
      } finally {
        setLoadingAuth(false);
      }
    }
    void loadCurrentUser();
  }, []);

  async function handleAuthSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      setSubmittingAuth(true);
      const response =
        authMode === "register"
          ? await registerDoctor(username.trim(), password, displayName.trim() || undefined)
          : await loginDoctor(username.trim(), password);
      setDoctor(response.doctor);
      setWorkspace("doctor");
      setPassword("");
      setAuthError(null);
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "登录失败，请检查账号密码。");
    } finally {
      setSubmittingAuth(false);
    }
  }

  async function handleLogout() {
    try {
      await logoutDoctor();
    } finally {
      setDoctor(null);
      setWorkspace(null);
    }
  }

  const activeWorkspace = workspace ?? "public";

  return (
    <main className="shell">
      <header className="hero hero--home">
        <div className="hero__content">
          <p className="hero__eyebrow">内部评估系统</p>
          <h1>功能医学营养评估与报告中心</h1>
          <p className="hero__copy">
            把报告上传、人工校对、营养素匹配、生活方式建议和审核发布收在一套紧凑的本地工作流里。
          </p>
          <div className="hero__chips">
            <span className="hero__chip">公共工作台</span>
            <span className="hero__chip">医生私人规则库</span>
            <span className="hero__chip">公共规则即时共享</span>
          </div>
        </div>

        <aside className="hero__panel">
          <p className="hero__label">进入工作台</p>
          {doctor ? (
            <div className="stack">
              <div className="info-note">
                <strong>{doctor.display_name}</strong>
                <p>
                  已登录账号 {doctor.username} · {doctor.role === "admin" ? "管理员" : "医生"}
                </p>
              </div>
              <div className="inline-actions">
                <button type="button" className="primary-button" onClick={() => setWorkspace("doctor")}>
                  进入我的工作台
                </button>
                <button type="button" className="secondary-button" onClick={() => setWorkspace("public")}>
                  使用公共工作台
                </button>
                <button type="button" className="secondary-button" onClick={() => void handleLogout()}>
                  退出登录
                </button>
              </div>
            </div>
          ) : (
            <div className="stack">
              <button type="button" className="primary-button" onClick={() => setWorkspace("public")}>
                进入公共工作台
              </button>
              <div className="auth-tabs">
                <button
                  type="button"
                  className={`auth-tab${authMode === "login" ? " auth-tab--active" : ""}`}
                  onClick={() => setAuthMode("login")}
                >
                  医生登录
                </button>
                <button
                  type="button"
                  className={`auth-tab${authMode === "register" ? " auth-tab--active" : ""}`}
                  onClick={() => setAuthMode("register")}
                >
                  注册账号
                </button>
              </div>
              <form className="stack" onSubmit={handleAuthSubmit}>
                <label className="field">
                  <span>医生账号</span>
                  <input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="例如：dr-zhang" />
                </label>
                {authMode === "register" ? (
                  <label className="field">
                    <span>医生姓名</span>
                    <input
                      value={displayName}
                      onChange={(event) => setDisplayName(event.target.value)}
                      placeholder="例如：张医生"
                    />
                  </label>
                ) : null}
                <label className="field">
                  <span>密码</span>
                  <input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="至少 6 位"
                  />
                </label>
                {authError ? <p className="error-text">{authError}</p> : null}
                <button className="primary-button" disabled={submittingAuth || loadingAuth}>
                  {submittingAuth ? "处理中..." : authMode === "register" ? "注册并进入" : "登录并进入"}
                </button>
              </form>
            </div>
          )}

          <div className="hero__stats">
            <Link href="/products" className="hero__stat hero__stat--link">
              <strong>SKU</strong>
              <span>产品规则</span>
            </Link>
            <Link href="/assistant" className="hero__stat hero__stat--link">
              <strong>AI</strong>
              <span>智慧助手</span>
            </Link>
            <Link href="/llm-config" className="hero__stat hero__stat--link">
              <strong>模型</strong>
              <span>API 配置</span>
            </Link>
          </div>
        </aside>
      </header>

      {workspace ? (
        <div className="stack">
          <section className="workspace-switcher">
            <div>
              <strong>{activeWorkspace === "doctor" ? "我的医生工作台" : "公共工作台"}</strong>
              <p className="muted">
                {activeWorkspace === "doctor"
                  ? "这里的病例只属于当前医生；生成报告会参考公共规则和你的私人规则。"
                  : "这里不需要登录即可使用；生成报告只参考公共规则，不能保存新的医生规则。"}
              </p>
            </div>
            <div className="inline-actions">
              <button type="button" className="secondary-button" onClick={() => setWorkspace("public")}>
                公共工作台
              </button>
              {doctor ? (
                <button type="button" className="secondary-button" onClick={() => setWorkspace("doctor")}>
                  我的工作台
                </button>
              ) : null}
            </div>
          </section>
          <DashboardLocal workspaceScope={activeWorkspace} currentDoctor={doctor} />
        </div>
      ) : null}
    </main>
  );
}
