"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchLlmConfig, updateLlmConfig } from "../lib/api";
import { LLMConfig } from "../lib/types";
import { SectionCard } from "./section-card";

type EditableLlmConfig = Omit<LLMConfig, "configured">;

const defaultConfig: EditableLlmConfig = {
  base_url: "",
  api_key: "",
  model: "",
  api_style: "auto",
  timeout_seconds: 45,
  temperature: 0.1
};

export function LlmConfigManager() {
  const [config, setConfig] = useState<EditableLlmConfig>(defaultConfig);
  const [configured, setConfigured] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        const nextConfig = await fetchLlmConfig();
        setConfig({
          base_url: nextConfig.base_url ?? "",
          api_key: nextConfig.api_key ?? "",
          model: nextConfig.model ?? "",
          api_style: nextConfig.api_style,
          timeout_seconds: nextConfig.timeout_seconds,
          temperature: nextConfig.temperature
        });
        setConfigured(nextConfig.configured);
        setError(nextConfig.validation_error ?? null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载大模型配置失败");
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, []);

  function updateField<K extends keyof EditableLlmConfig>(key: K, value: EditableLlmConfig[K]) {
    setConfig((current) => ({ ...current, [key]: value }));
  }

  async function handleSave() {
    try {
      setSaving(true);
      const saved = await updateLlmConfig({
        base_url: config.base_url?.trim() || null,
        api_key: config.api_key?.trim() || null,
        model: config.model?.trim() || null,
        api_style: config.api_style,
        timeout_seconds: config.timeout_seconds,
        temperature: config.temperature
      });
      setConfig({
        base_url: saved.base_url ?? "",
        api_key: saved.api_key ?? "",
        model: saved.model ?? "",
        api_style: saved.api_style,
        timeout_seconds: saved.timeout_seconds,
        temperature: saved.temperature
      });
      setConfigured(saved.configured);
      setNotice(
        saved.configured
          ? "已保存并重新加载大模型配置。"
          : "配置已保存，当前仍将回退为本地模式。"
      );
      setError(saved.validation_error ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存大模型配置失败");
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
          <h1>大模型配置</h1>
          <p className="hero__copy">
            在这里直接配置甲方自己的大模型接口。保存后系统会重新加载模型能力，后续识别和报告润色会优先使用这里的配置。
          </p>
        </div>
        <div className="hero__panel">
          <p className="hero__label">当前状态</p>
          <div className="hero__stats">
            <div className="hero__stat">
              <strong>{configured ? "已启用" : "本地"}</strong>
              <span>模型模式</span>
            </div>
            <div className="hero__stat">
              <strong>{config.model || "未填写"}</strong>
              <span>当前模型</span>
            </div>
            <div className="hero__stat">
              <strong>{config.api_style}</strong>
              <span>接口风格</span>
            </div>
          </div>
        </div>
      </header>

      <SectionCard title="接口参数" subtitle="LLM settings">
        {loading ? <p className="muted">正在加载大模型配置...</p> : null}
        {error ? <p className="error-text">{error}</p> : null}
        {notice ? <div className="info-note"><p>{notice}</p></div> : null}
        <div className="stack">
          <div className="info-note">
            <strong>先填写这三项就可以</strong>
            <p>对大多数甲方来说，只需要填写 Base URL、API Key 和模型名。接口风格、超时时间、温度都可以先保持默认。</p>
            <p>注意：API Key 不是服务地址，不要把 Base URL 粘贴到 API Key 栏；如果 Key 以 Bearer 开头，系统会自动去掉 Bearer 前缀。</p>
          </div>

          <label className="field">
            <span>Base URL</span>
            <input
              value={config.base_url ?? ""}
              onChange={(event) => updateField("base_url", event.target.value)}
              placeholder="例如：https://ark.cn-beijing.volces.com/api/v3"
            />
          </label>

          <label className="field">
            <span>API Key</span>
            <input
              type="password"
              value={config.api_key ?? ""}
              onChange={(event) => updateField("api_key", event.target.value)}
              placeholder="粘贴甲方自己的 API Key"
            />
          </label>

          <label className="field">
            <span>模型名</span>
            <input
              value={config.model ?? ""}
              onChange={(event) => updateField("model", event.target.value)}
              placeholder="例如：doubao-seed-2-0-lite-250821"
            />
          </label>

          <button
            type="button"
            className="secondary-button"
            onClick={() => setShowAdvanced((current) => !current)}
          >
            {showAdvanced ? "收起高级设置" : "展开高级设置"}
          </button>

          {showAdvanced ? (
            <div className="stack">
              <div className="info-note">
                <strong>高级设置说明</strong>
                <p>只有在你明确知道自己在用哪种接口协议，或者遇到超时、输出不稳定时，才需要调整这些参数。</p>
              </div>

              <div className="grid-two">
                <label className="field">
                  <span>接口风格</span>
                  <select value={config.api_style} onChange={(event) => updateField("api_style", event.target.value)}>
                    <option value="auto">auto（推荐）</option>
                    <option value="responses">responses</option>
                    <option value="chat">chat</option>
                  </select>
                </label>
                <label className="field">
                  <span>超时时间（秒）</span>
                  <input
                    type="number"
                    min={10}
                    max={180}
                    step={1}
                    value={config.timeout_seconds}
                    onChange={(event) => updateField("timeout_seconds", Number(event.target.value) || 45)}
                  />
                </label>
              </div>

              <label className="field">
                <span>温度</span>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.1}
                  value={config.temperature}
                  onChange={(event) => updateField("temperature", Number(event.target.value) || 0.1)}
                />
              </label>
            </div>
          ) : null}

          <div className="info-note">
            <strong>说明</strong>
            <p>只要填写完整的 Base URL、API Key 和模型名，系统就会优先使用这里的大模型；留空则自动回退为本地模式。</p>
          </div>

          <button type="button" className="primary-button" disabled={saving} onClick={() => void handleSave()}>
            {saving ? "正在保存并重载..." : "保存大模型配置"}
          </button>
        </div>
      </SectionCard>
    </main>
  );
}
