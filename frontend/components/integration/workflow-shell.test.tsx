import React from "react";
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
        steps={[
          { id: "case", state: "complete" },
          { id: "attachments", state: "current" },
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
    expect(html).toContain('aria-current="step"');
    expect(html).toContain('aria-disabled="true" tabindex="-1"');
    expect(html).not.toContain('workflow-step-analysis');
    expect(html).toContain('data-step="attachments"');
    expect(html).toContain('role="alert"');
  });
});
