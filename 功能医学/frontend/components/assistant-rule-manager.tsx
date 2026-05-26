"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  deleteClinicianRule,
  fetchCurrentUser,
  fetchClinicianRules,
  fetchProductCatalog,
  updateClinicianRule
} from "../lib/api";
import { ClinicianRule, DoctorAccount, ProductRule } from "../lib/types";
import { SectionCard } from "./section-card";

function joinLines(values: string[]) {
  return values.join("\n");
}

function splitLines(value: string) {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function AssistantRuleManager() {
  const [rules, setRules] = useState<ClinicianRule[]>([]);
  const [products, setProducts] = useState<ProductRule[]>([]);
  const [currentDoctor, setCurrentDoctor] = useState<DoctorAccount | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ClinicianRule | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        const [auth, nextRules, nextProducts] = await Promise.all([
          fetchCurrentUser(),
          fetchClinicianRules(),
          fetchProductCatalog()
        ]);
        setCurrentDoctor(auth.doctor ?? null);
        setRules(nextRules);
        setProducts(nextProducts);
        const first = nextRules[0] ?? null;
        setSelectedId(first?.id ?? null);
        setDraft(first ?? null);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载智慧助手规则失败");
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, []);

  const enabledCount = useMemo(() => rules.filter((item) => item.enabled).length, [rules]);
  const productLookup = useMemo(() => {
    return new Map(products.map((item) => [item.sku_id, item.display_name]));
  }, [products]);

  function selectRule(rule: ClinicianRule) {
    setSelectedId(rule.id);
    setDraft(rule);
    setNotice(null);
    setError(null);
  }

  function updateDraft<K extends keyof ClinicianRule>(key: K, value: ClinicianRule[K]) {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  }

  async function handleSave() {
    if (!draft) {
      return;
    }
    if (!currentDoctor) {
      setError("请先登录医生账号后再修改医生规则。");
      setNotice(null);
      return;
    }

    const targetSkuIds = draft.target_sku_ids.map((item) => item.trim()).filter(Boolean);
    if (!draft.title.trim()) {
      setError("请先填写规则标题。");
      setNotice(null);
      return;
    }
    if (!draft.instruction_text.trim()) {
      setError("请先填写医生经验说明。");
      setNotice(null);
      return;
    }
    if (targetSkuIds.length === 0) {
      setError("请至少保留一个目标产品 sku_id。");
      setNotice(null);
      return;
    }

    const unknownSkuIds = targetSkuIds.filter((skuId) => !productLookup.has(skuId));
    if (unknownSkuIds.length > 0) {
      setError(`以下 sku_id 不在当前产品目录中：${unknownSkuIds.join("、")}`);
      setNotice(null);
      return;
    }

    try {
      setSaving(true);
      const updated = await updateClinicianRule({
        ...draft,
        title: draft.title.trim(),
        instruction_text: draft.instruction_text.trim(),
        strength: Number.isFinite(draft.strength) ? Math.min(Math.max(draft.strength, 0.2), 5) : 1,
        target_sku_ids: targetSkuIds,
        trigger_marker_rules: draft.trigger_marker_rules.map((item) => item.trim()).filter(Boolean),
        trigger_support_profiles: draft.trigger_support_profiles.map((item) => item.trim()).filter(Boolean),
        trigger_goals: draft.trigger_goals.map((item) => item.trim()).filter(Boolean),
        trigger_symptoms: draft.trigger_symptoms.map((item) => item.trim()).filter(Boolean),
        trigger_chief_concerns: draft.trigger_chief_concerns.map((item) => item.trim()).filter(Boolean),
        trigger_conditions: draft.trigger_conditions.map((item) => item.trim()).filter(Boolean),
        notes: draft.notes?.trim() || null
      });
      setRules((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setSelectedId(updated.id);
      setDraft(updated);
      setNotice(`已保存规则：${updated.title}。后续相似病例重新生成草案时会自动读取最新版本。`);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存智慧助手规则失败");
      setNotice(null);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!draft) {
      return;
    }
    if (!currentDoctor) {
      setError("请先登录医生账号后再删除医生规则。");
      setNotice(null);
      return;
    }

    if (typeof window !== "undefined") {
      const confirmed = window.confirm(`确认删除规则“${draft.title}”吗？删除后后续病例将不再参考它。`);
      if (!confirmed) {
        return;
      }
    }

    try {
      setSaving(true);
      await deleteClinicianRule(draft.id);
      const currentIndex = rules.findIndex((item) => item.id === draft.id);
      const remainingRules = rules.filter((item) => item.id !== draft.id);
      setRules(remainingRules);
      const nextRule = remainingRules[currentIndex] ?? remainingRules[currentIndex - 1] ?? null;
      setSelectedId(nextRule?.id ?? null);
      setDraft(nextRule);
      setNotice(`已删除规则：${draft.title}`);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除智慧助手规则失败");
      setNotice(null);
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="shell">
      <header className="workbench__hero">
        <div>
          <Link href="/" className="back-link">
            返回工作台
          </Link>
          <h1>AI 智慧助手规则</h1>
          <p className="hero__copy">
            医生可以把单个病例里沉淀出来的经验，调整成可复用的规则。保存后，后续相似病例在重新生成草案时，
            系统会自动把这些规则作为推荐加权依据。
          </p>
        </div>
        <div className="hero__panel">
          <p className="hero__label">当前范围</p>
          <div className="hero__stats">
            <div className="hero__stat">
              <strong>{rules.length}</strong>
              <span>规则条目</span>
            </div>
            <div className="hero__stat">
              <strong>{enabledCount}</strong>
              <span>已启用</span>
            </div>
              <div className="hero__stat">
                <strong>{products.length}</strong>
                <span>可选产品</span>
              </div>
            </div>
            {!currentDoctor ? (
              <div className="info-note">
                <strong>只读模式</strong>
                <p>当前未登录医生账号，可以查看公共规则；如需修改、停用或删除规则，请先回首页登录。</p>
              </div>
            ) : null}
          </div>
      </header>

      <div className="dashboard-grid product-grid">
        <SectionCard title="规则列表" subtitle="Doctor memory rules">
          {error ? <p className="error-text">{error}</p> : null}
          {loading ? <p className="muted">正在加载智慧助手规则...</p> : null}
          <div className="stack">
            <div className="info-note">
              <strong>如何新增</strong>
              <p>新增规则仍建议从具体病例工作台发起，这样助手会自动带上该病例的关键指标、症状和系统信号。</p>
              <p>来到这里后，你可以继续微调目标产品、触发条件、权重和启停状态。</p>
            </div>
            <div className="product-list">
              {rules.map((rule, index) => (
                <button
                  key={rule.id}
                  type="button"
                  className={`product-list__item${rule.id === selectedId ? " product-list__item--active" : ""}`}
                  onClick={() => selectRule(rule)}
                >
                  <strong>
                    <span className="product-list__index">{String(index + 1).padStart(2, "0")}.</span> {rule.title}
                  </strong>
                  <span>
                    {rule.scope === "public" ? "公共规则" : "私人规则"} · {rule.enabled ? "已启用" : "已停用"} ·{" "}
                    {rule.action === "avoid" ? "谨慎/抑制" : "优先/增强"}
                  </span>
                  <small>目标产品 {rule.target_sku_ids.join("、")}</small>
                </button>
              ))}
            </div>
            {!loading && rules.length === 0 ? (
              <p className="muted">还没有沉淀好的医生规则。可以先进入具体病例，在“AI 智慧助手”里记录第一条经验。</p>
            ) : null}
          </div>
        </SectionCard>

        <SectionCard title="规则编辑" subtitle="Editable fields">
          {!draft ? <p className="muted">请先从左侧选择一条规则后再编辑。</p> : null}
          {draft ? (
            <div className="stack">
              {notice ? (
                <div className="info-note">
                  <p>{notice}</p>
                </div>
              ) : null}

              <div className="info-note">
                <p>
                  这里改的是“相似病例如何加权”的规则，不会回写已经发布过的旧报告；它只会影响你之后重新生成的新草案。
                </p>
              </div>

              <div className="grid-two">
                <label className="field">
                  <span>规则标题</span>
                  <input value={draft.title} onChange={(event) => updateDraft("title", event.target.value)} />
                </label>
                <label className="field checkbox">
                  <input
                    type="checkbox"
                    checked={draft.enabled}
                    onChange={(event) => updateDraft("enabled", event.target.checked)}
                  />
                    <span>启用这条医生规则</span>
                  </label>
                  <div className="info-note">
                    <strong>{draft.scope === "public" ? "公共规则" : "私人规则"}</strong>
                    <p>
                      创建人：{draft.created_by || "未知"}。
                      {draft.scope === "public"
                        ? "公共规则会参与所有医生和公共工作台的后续报告生成。"
                        : "私人规则只会参与所属医生工作台的后续报告生成。"}
                    </p>
                  </div>
                </div>

              <div className="grid-two">
                <label className="field">
                  <span>动作类型</span>
                  <select
                    value={draft.action}
                    onChange={(event) => updateDraft("action", event.target.value as ClinicianRule["action"])}
                  >
                    <option value="boost">优先推荐 boost</option>
                    <option value="avoid">谨慎处理 avoid</option>
                  </select>
                </label>
                <label className="field">
                  <span>规则强度</span>
                  <input
                    type="number"
                    min={0.2}
                    max={5}
                    step={0.1}
                    value={draft.strength}
                    onChange={(event) => updateDraft("strength", Number(event.target.value) || 1)}
                  />
                </label>
              </div>

              <label className="field">
                <span>医生经验说明</span>
                <textarea
                  rows={4}
                  value={draft.instruction_text}
                  onChange={(event) => updateDraft("instruction_text", event.target.value)}
                />
              </label>

              <label className="field">
                <span>目标产品 sku_id（每行一个）</span>
                <textarea
                  rows={4}
                  value={joinLines(draft.target_sku_ids)}
                  onChange={(event) => updateDraft("target_sku_ids", splitLines(event.target.value))}
                />
              </label>

              <div className="info-note">
                <strong>当前可选产品</strong>
                <p>
                  {products.length === 0
                    ? "尚未加载到产品目录。"
                    : products.map((item) => `${item.sku_id}（${item.display_name}）`).join("；")}
                </p>
              </div>

              <div className="grid-two">
                <label className="field">
                  <span>触发指标（每行一个）</span>
                  <textarea
                    rows={5}
                    value={joinLines(draft.trigger_marker_rules)}
                    onChange={(event) => updateDraft("trigger_marker_rules", splitLines(event.target.value))}
                  />
                </label>
                <label className="field">
                  <span>系统信号（每行一个）</span>
                  <textarea
                    rows={5}
                    value={joinLines(draft.trigger_support_profiles)}
                    onChange={(event) => updateDraft("trigger_support_profiles", splitLines(event.target.value))}
                  />
                </label>
              </div>

              <div className="grid-two">
                <label className="field">
                  <span>目标 goals（每行一个）</span>
                  <textarea
                    rows={4}
                    value={joinLines(draft.trigger_goals)}
                    onChange={(event) => updateDraft("trigger_goals", splitLines(event.target.value))}
                  />
                </label>
                <label className="field">
                  <span>症状 symptoms（每行一个）</span>
                  <textarea
                    rows={4}
                    value={joinLines(draft.trigger_symptoms)}
                    onChange={(event) => updateDraft("trigger_symptoms", splitLines(event.target.value))}
                  />
                </label>
              </div>

              <div className="grid-two">
                <label className="field">
                  <span>主诉 chief concerns（每行一个）</span>
                  <textarea
                    rows={4}
                    value={joinLines(draft.trigger_chief_concerns)}
                    onChange={(event) => updateDraft("trigger_chief_concerns", splitLines(event.target.value))}
                  />
                </label>
                <label className="field">
                  <span>疾病/条件 conditions（每行一个）</span>
                  <textarea
                    rows={4}
                    value={joinLines(draft.trigger_conditions)}
                    onChange={(event) => updateDraft("trigger_conditions", splitLines(event.target.value))}
                  />
                </label>
              </div>

              <label className="field">
                <span>备注</span>
                <textarea
                  rows={3}
                  value={draft.notes ?? ""}
                  onChange={(event) => updateDraft("notes", event.target.value)}
                />
              </label>

              <div className="grid-two">
                <button type="button" className="primary-button" disabled={saving || !currentDoctor} onClick={() => void handleSave()}>
                  {saving ? "正在保存..." : "保存智慧助手规则"}
                </button>
                <button
                  type="button"
                  className="secondary-button secondary-button--danger"
                    disabled={saving || !currentDoctor}
                  onClick={() => void handleDelete()}
                >
                  删除规则
                </button>
              </div>
            </div>
          ) : null}
        </SectionCard>
      </div>
    </main>
  );
}
