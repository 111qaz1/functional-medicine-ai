"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { createProductRule, deleteProductRule, fetchProductCatalog, updateProductRule } from "../lib/api";
import { ProductRule } from "../lib/types";
import { SectionCard } from "./section-card";

const NEW_PRODUCT_ID = "__new_product__";

function joinLines(values: string[]) {
  return values.join("\n");
}

function splitLines(value: string) {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function createEmptyProduct(): ProductRule {
  return {
    sku_id: "",
    display_name: "",
    category: "general_support",
    source_refs: [],
    formula_summary: "",
    core_ingredients: [],
    candidate_use_cases: [],
    contraindications: [],
    enabled: true,
    merge_status: null,
    indications: [],
    exclusions: [],
    dosage_rule: "",
    interaction_rule: [],
    warning_text: [],
    lifestyle_tags: [],
    priority: 50
  };
}

export function ProductCatalogManager() {
  const [products, setProducts] = useState<ProductRule[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ProductRule | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        const nextProducts = await fetchProductCatalog();
        setProducts(nextProducts);
        const first = nextProducts[0] ?? null;
        setSelectedId(first?.sku_id ?? null);
        setDraft(first ?? null);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载产品目录失败");
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, []);

  const isCreating = selectedId === NEW_PRODUCT_ID;
  const enabledCount = useMemo(() => products.filter((item) => item.enabled).length, [products]);
  const pendingMergeCount = useMemo(
    () => products.filter((item) => item.merge_status && item.merge_status.trim()).length,
    [products]
  );

  function selectProduct(product: ProductRule) {
    setSelectedId(product.sku_id);
    setDraft(product);
    setNotice(null);
    setError(null);
  }

  function startCreateProduct() {
    setSelectedId(NEW_PRODUCT_ID);
    setDraft(createEmptyProduct());
    setNotice("已进入新增产品模式。保存后，新产品会立刻进入后续推荐候选范围。");
    setError(null);
  }

  function restoreSelection(nextProducts: ProductRule[]) {
    const fallback = nextProducts[0] ?? null;
    setSelectedId(fallback?.sku_id ?? null);
    setDraft(fallback);
  }

  function cancelCreateProduct() {
    setNotice(null);
    setError(null);
    restoreSelection(products);
  }

  function updateDraft<K extends keyof ProductRule>(key: K, value: ProductRule[K]) {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  }

  async function handleSave() {
    if (!draft) {
      return;
    }

    const normalizedSkuId = draft.sku_id.trim();
    if (!normalizedSkuId) {
      setError("请先填写 sku_id。建议使用英文、小写和下划线，例如 sku_custom_focus_support。");
      setNotice(null);
      return;
    }
    if (!draft.display_name.trim()) {
      setError("请先填写产品名称。");
      setNotice(null);
      return;
    }
    if (!draft.formula_summary.trim()) {
      setError("请先填写配方摘要。");
      setNotice(null);
      return;
    }
    if (!draft.dosage_rule.trim()) {
      setError("请先填写剂量规则。");
      setNotice(null);
      return;
    }
    if (isCreating && products.some((item) => item.sku_id === normalizedSkuId)) {
      setError(`sku_id ${normalizedSkuId} 已存在，请更换后再保存。`);
      setNotice(null);
      return;
    }

    const payload = { ...draft, sku_id: normalizedSkuId };

    try {
      setSaving(true);
      if (isCreating) {
        const created = await createProductRule(payload);
        setProducts((current) => [...current, created]);
        setSelectedId(created.sku_id);
        setDraft(created);
        setNotice(`已新增 ${created.display_name}。后续重新生成草案时，系统会自动把它纳入候选产品规则。`);
      } else {
        const updated = await updateProductRule(payload);
        setProducts((current) => current.map((item) => (item.sku_id === updated.sku_id ? updated : item)));
        setSelectedId(updated.sku_id);
        setDraft(updated);
        setNotice(`已保存 ${updated.display_name} 的规则修改。后续生成草案会自动使用这里的最新配置。`);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存产品规则失败");
      setNotice(null);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!draft) {
      return;
    }

    if (isCreating) {
      cancelCreateProduct();
      return;
    }

    if (typeof window !== "undefined") {
      const confirmed = window.confirm(
        `确认删除产品“${draft.display_name}”吗？删除后，它将不再进入后续健康报告的推荐候选。`
      );
      if (!confirmed) {
        return;
      }
    }

    try {
      setSaving(true);
      await deleteProductRule(draft.sku_id);
      const currentIndex = products.findIndex((item) => item.sku_id === draft.sku_id);
      const remainingProducts = products.filter((item) => item.sku_id !== draft.sku_id);
      setProducts(remainingProducts);
      const nextProduct = remainingProducts[currentIndex] ?? remainingProducts[currentIndex - 1] ?? null;
      setSelectedId(nextProduct?.sku_id ?? null);
      setDraft(nextProduct);
      setNotice(`已删除 ${draft.display_name}。后续新生成的草案将不会再推荐这个产品。`);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除产品失败");
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
          <h1>产品规则管理</h1>
          <p className="hero__copy">
            在本地直接新增、修改或删除产品规则。保存后，后续新生成的健康报告会自动读取这里的最新产品目录。
          </p>
        </div>
        <div className="hero__panel">
          <p className="hero__label">当前范围</p>
          <div className="hero__stats">
            <div className="hero__stat">
              <strong>{products.length}</strong>
              <span>产品条目</span>
            </div>
            <div className="hero__stat">
              <strong>{enabledCount}</strong>
              <span>已启用</span>
            </div>
            <div className="hero__stat">
              <strong>{pendingMergeCount}</strong>
              <span>待确认规格</span>
            </div>
          </div>
        </div>
      </header>

      <div className="dashboard-grid product-grid">
        <SectionCard title="产品列表" subtitle="Product catalog">
          {error ? <p className="error-text">{error}</p> : null}
          {loading ? <p className="muted">正在加载产品目录...</p> : null}
          <div className="stack">
            <button type="button" className="primary-button" onClick={startCreateProduct} disabled={saving}>
              新增产品
            </button>
            <div className="product-list">
              {products.map((product, index) => (
                <button
                  key={product.sku_id}
                  type="button"
                  className={`product-list__item${product.sku_id === selectedId ? " product-list__item--active" : ""}`}
                  onClick={() => selectProduct(product)}
                >
                  <strong>
                    <span className="product-list__index">{String(index + 1).padStart(2, "0")}.</span> {product.display_name}
                  </strong>
                  <span>{product.category}</span>
                  <small>{product.enabled ? "已启用" : "未启用"}</small>
                </button>
              ))}
            </div>
          </div>
        </SectionCard>

        <SectionCard title={isCreating ? "新增产品" : "规则编辑"} subtitle={isCreating ? "Create product" : "Editable fields"}>
          {!draft ? <p className="muted">请选择一个产品后再编辑，或点击左侧“新增产品”。</p> : null}
          {draft ? (
            <div className="stack">
              {notice ? (
                <div className="info-note">
                  <p>{notice}</p>
                </div>
              ) : null}
              <div className="info-note">
                <p>
                  保存后，下一次生成草案会自动使用这里的最新产品配置。`category`、`formula_summary`、
                  `core_ingredients`、`candidate_use_cases`、`indications`、`exclusions`、
                  `lifestyle_tags`、`priority` 和 `enabled` 都会联动影响智能推荐结果。
                </p>
                <p>
                  新增产品时，建议把 `indications`、`candidate_use_cases` 和 `formula_summary`
                  写清楚，这样推荐引擎才更容易把新产品纳入后续健康报告。
                </p>
              </div>

              <div className="grid-two">
                <label className="field">
                  <span>sku_id</span>
                  <input
                    value={draft.sku_id}
                    disabled={!isCreating}
                    placeholder="例如：sku_custom_focus_support"
                    onChange={(event) => updateDraft("sku_id", event.target.value)}
                  />
                </label>
                <label className="field">
                  <span>产品名称</span>
                  <input value={draft.display_name} onChange={(event) => updateDraft("display_name", event.target.value)} />
                </label>
              </div>

              <div className="grid-two">
                <label className="field">
                  <span>分类</span>
                  <input value={draft.category} onChange={(event) => updateDraft("category", event.target.value)} />
                </label>
                <label className="field">
                  <span>合并状态</span>
                  <input
                    value={draft.merge_status ?? ""}
                    placeholder="可留空，例如 pending_spec_decision"
                    onChange={(event) => updateDraft("merge_status", event.target.value || null)}
                  />
                </label>
              </div>

              <div className="grid-two">
                <label className="field">
                  <span>优先级</span>
                  <input
                    type="number"
                    value={draft.priority}
                    onChange={(event) => updateDraft("priority", Number(event.target.value) || 0)}
                  />
                </label>
                <label className="field checkbox">
                  <input
                    type="checkbox"
                    checked={draft.enabled}
                    onChange={(event) => updateDraft("enabled", event.target.checked)}
                  />
                  <span>启用此产品规则</span>
                </label>
              </div>

              <label className="field">
                <span>配方摘要</span>
                <textarea
                  rows={4}
                  value={draft.formula_summary}
                  onChange={(event) => updateDraft("formula_summary", event.target.value)}
                />
              </label>

              <div className="grid-two">
                <label className="field">
                  <span>来源引用（每行一个）</span>
                  <textarea
                    rows={4}
                    value={joinLines(draft.source_refs)}
                    onChange={(event) => updateDraft("source_refs", splitLines(event.target.value))}
                  />
                </label>
                <label className="field">
                  <span>核心成分（每行一个）</span>
                  <textarea
                    rows={4}
                    value={joinLines(draft.core_ingredients)}
                    onChange={(event) => updateDraft("core_ingredients", splitLines(event.target.value))}
                  />
                </label>
              </div>

              <div className="grid-two">
                <label className="field">
                  <span>候选使用场景（每行一个）</span>
                  <textarea
                    rows={5}
                    value={joinLines(draft.candidate_use_cases)}
                    onChange={(event) => updateDraft("candidate_use_cases", splitLines(event.target.value))}
                  />
                </label>
                <label className="field">
                  <span>适用规则 indications（每行一个）</span>
                  <textarea
                    rows={5}
                    value={joinLines(draft.indications)}
                    onChange={(event) => updateDraft("indications", splitLines(event.target.value))}
                  />
                </label>
              </div>

              <div className="grid-two">
                <label className="field">
                  <span>排除规则 exclusions（每行一个）</span>
                  <textarea
                    rows={5}
                    value={joinLines(draft.exclusions)}
                    onChange={(event) => updateDraft("exclusions", splitLines(event.target.value))}
                  />
                </label>
                <label className="field">
                  <span>禁忌说明（每行一个）</span>
                  <textarea
                    rows={5}
                    value={joinLines(draft.contraindications)}
                    onChange={(event) => updateDraft("contraindications", splitLines(event.target.value))}
                  />
                </label>
              </div>

              <label className="field">
                <span>剂量规则</span>
                <textarea
                  rows={3}
                  value={draft.dosage_rule}
                  onChange={(event) => updateDraft("dosage_rule", event.target.value)}
                />
              </label>

              <div className="grid-two">
                <label className="field">
                  <span>相互作用提示（每行一个）</span>
                  <textarea
                    rows={4}
                    value={joinLines(draft.interaction_rule)}
                    onChange={(event) => updateDraft("interaction_rule", splitLines(event.target.value))}
                  />
                </label>
                <label className="field">
                  <span>警示文案（每行一个）</span>
                  <textarea
                    rows={4}
                    value={joinLines(draft.warning_text)}
                    onChange={(event) => updateDraft("warning_text", splitLines(event.target.value))}
                  />
                </label>
              </div>

              <label className="field">
                <span>生活方式标签（每行一个）</span>
                <textarea
                  rows={3}
                  value={joinLines(draft.lifestyle_tags)}
                  onChange={(event) => updateDraft("lifestyle_tags", splitLines(event.target.value))}
                />
              </label>

              <div className="grid-two">
                <button type="button" className="primary-button" disabled={saving} onClick={() => void handleSave()}>
                  {saving ? "正在保存..." : isCreating ? "创建产品并保存规则" : "保存产品规则"}
                </button>
                <button
                  type="button"
                  className="secondary-button secondary-button--danger"
                  disabled={saving}
                  onClick={() => void handleDelete()}
                >
                  {isCreating ? "取消新增" : "删除产品"}
                </button>
              </div>
            </div>
          ) : null}
        </SectionCard>
      </div>
    </main>
  );
}
