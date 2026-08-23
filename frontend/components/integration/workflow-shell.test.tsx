import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { WorkflowNotice, WorkflowSection, WorkflowShell } from "./workflow-shell";

describe("WorkflowShell", () => {
  it("exposes stable semantic adaptation hooks without business-specific styling", () => {
    const html = renderToStaticMarkup(
      <WorkflowShell
        title="虚构病例"
        caseId="case_fixture"
        currentStep="attachments"
        theme="test"
        brandSlot={<span>测试品牌</span>}
        contextSlot={<span>测试病例上下文</span>}
        steps={[
          { id: "case", state: "complete" },
          { id: "attachments", state: "current" },
          { id: "analysis", state: "blocked" },
          { id: "review", state: "blocked" },
          { id: "draft", state: "blocked" },
          { id: "report", state: "blocked" }
        ]}
      >
        <WorkflowSection id="attachments" title="资料" state="current">
          <WorkflowNotice tone="error">上传失败</WorkflowNotice>
        </WorkflowSection>
      </WorkflowShell>
    );

    expect(html).toContain('class="workflow-app"');
    expect(html).toContain('data-theme="test"');
    expect(html).toContain('data-current-step="attachments"');
    expect(html).toContain("测试品牌");
    expect(html).toContain("测试病例上下文");
    expect(html).toContain("第 <strong>2</strong> / 6 步");
    expect(html).toContain('aria-current="step"');
    expect(html).toContain('aria-disabled="true" tabindex="-1"');
    expect(html).not.toContain('href="#workflow-step-analysis"');
    expect(html).toContain('data-step="attachments"');
    expect(html).toContain('role="alert"');
  });

  it("uses the PARACELSUS theme by default without coupling it to fixture mode", () => {
    const html = renderToStaticMarkup(
      <WorkflowShell
        title="开始病例工作流"
        currentStep="case"
        steps={[
          { id: "case", state: "current" },
          { id: "attachments", state: "blocked" },
          { id: "analysis", state: "blocked" },
          { id: "review", state: "blocked" },
          { id: "draft", state: "blocked" },
          { id: "report", state: "blocked" }
        ]}
      >
        <WorkflowSection id="case" title="病例" state="current">内容</WorkflowSection>
      </WorkflowShell>
    );

    expect(html).toContain('data-theme="paracelsus"');
    expect(html).toContain("功能医学对接工作台");
    expect(html).toContain('aria-label="病例：当前步骤"');
  });

  it("keeps blocked section copy readable instead of fading the whole section", () => {
    const styles = readFileSync(join(process.cwd(), "app/integration/workflow.css"), "utf8");
    const blockedRule = styles.match(/\.workflow-section\[data-state="blocked"\]\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(blockedRule).not.toContain("opacity");
    expect(blockedRule).toContain("background: var(--workflow-surface-muted)");
  });
});
