"use client";

import Link from "next/link";
import { ReactNode, useEffect, useState } from "react";

import { fetchCurrentUser } from "../lib/api";

export function AdminGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"loading" | "allowed" | "denied">("loading");

  useEffect(() => {
    void fetchCurrentUser()
      .then((response) => setState(response.doctor?.role === "admin" ? "allowed" : "denied"))
      .catch(() => setState("denied"));
  }, []);

  if (state === "loading") return <main className="shell"><p className="muted">正在验证管理员权限……</p></main>;
  if (state === "denied") {
    return (
      <main className="shell">
        <section className="section-card admin-gate">
          <p className="section-card__eyebrow">Admin only</p>
          <h1>此区域仅管理员可修改</h1>
          <p className="muted">请先用管理员账号登录。普通医生仍可使用病例分析，但不能修改产品规则、助手规则或模型 API 配置。</p>
          <Link className="primary-button" href="/">返回登录页</Link>
        </section>
      </main>
    );
  }
  return <>{children}</>;
}
