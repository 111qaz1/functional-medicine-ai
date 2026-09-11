"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import {
  createDoctor,
  fetchCurrentUser,
  fetchDoctors,
  resetDoctorPassword,
  updateDoctor
} from "../lib/api";
import type { DoctorAccount } from "../lib/types";

export function DoctorManagement() {
  const [currentDoctor, setCurrentDoctor] = useState<DoctorAccount | null>(null);
  const [doctors, setDoctors] = useState<DoctorAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const session = await fetchCurrentUser();
      const doctor = session.doctor ?? null;
      setCurrentDoctor(doctor);
      if (doctor?.role === "admin") setDoctors(await fetchDoctors());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法加载医生账号。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusyId("create");
    setError(null);
    try {
      await createDoctor(
        String(data.get("username") ?? "").trim(),
        String(data.get("password") ?? ""),
        String(data.get("display_name") ?? "").trim()
      );
      form.reset();
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建医生账号失败。");
    } finally {
      setBusyId(null);
    }
  }

  async function toggleDoctor(doctor: DoctorAccount) {
    setBusyId(doctor.id);
    setError(null);
    try {
      await updateDoctor(doctor.id, { enabled: !doctor.enabled });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更新账号状态失败。");
    } finally {
      setBusyId(null);
    }
  }

  async function resetPassword(doctor: DoctorAccount) {
    const password = window.prompt(`为 ${doctor.display_name} 设置临时密码（至少 6 位）：`);
    if (!password) return;
    setBusyId(doctor.id);
    setError(null);
    try {
      await resetDoctorPassword(doctor.id, password);
      window.alert("密码已重置，该医生的旧会话已失效。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "重置密码失败。");
    } finally {
      setBusyId(null);
    }
  }

  if (loading) return <main className="shell"><p>正在加载账号管理…</p></main>;
  if (!currentDoctor) return <main className="shell"><p>请先返回首页登录管理员账号。</p><Link href="/">返回首页</Link></main>;
  if (currentDoctor.role !== "admin") return <main className="shell"><p>只有管理员可以维护医生账号。</p><a href="/integration/cases">返回工作台</a></main>;

  return (
    <main className="shell stack">
      <section className="workspace-switcher">
        <div><strong>医生账号管理</strong><p className="muted">账号停用和密码重置会立即使该医生的旧会话失效。</p></div>
        <a className="secondary-button" href="/integration/cases">返回 AI 工作台</a>
      </section>
      {error ? <p className="error-text">{error}</p> : null}
      <section className="maintenance-panel stack">
        <h2>创建医生账号</h2>
        <form className="stack" onSubmit={(event) => void handleCreate(event)}>
          <label className="field"><span>医生账号</span><input name="username" required autoComplete="off" /></label>
          <label className="field"><span>医生姓名</span><input name="display_name" required autoComplete="off" /></label>
          <label className="field"><span>临时密码</span><input name="password" type="password" required minLength={6} autoComplete="new-password" /></label>
          <button className="primary-button" type="submit" disabled={busyId === "create"}>{busyId === "create" ? "正在创建…" : "创建医生账号"}</button>
        </form>
      </section>
      <section className="maintenance-panel stack">
        <h2>现有账号</h2>
        {doctors.map((doctor) => (
          <div className="workspace-switcher" key={doctor.id}>
            <div><strong>{doctor.display_name}</strong><p className="muted">{doctor.username} · {doctor.role === "admin" ? "管理员" : "医生"} · {doctor.enabled ? "启用" : "停用"}</p></div>
            <div className="inline-actions">
              <button className="secondary-button" type="button" disabled={busyId === doctor.id || doctor.id === currentDoctor.id} onClick={() => void toggleDoctor(doctor)}>{doctor.enabled ? "停用" : "启用"}</button>
              <button className="secondary-button" type="button" disabled={busyId === doctor.id} onClick={() => void resetPassword(doctor)}>重置密码</button>
            </div>
          </div>
        ))}
      </section>
    </main>
  );
}
