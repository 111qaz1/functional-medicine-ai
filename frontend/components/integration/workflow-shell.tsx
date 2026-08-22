import React from "react";
import type { ReactNode } from "react";

import { workflowCopy } from "../../lib/api-v2/copy";
import type { WorkflowStep, WorkflowStepId, WorkflowStepState } from "../../lib/api-v2/workflow-state";

export interface WorkflowShellProps {
  title: string;
  description?: string;
  caseId?: string;
  steps: WorkflowStep[];
  currentStep: WorkflowStepId;
  headerActions?: ReactNode;
  children: ReactNode;
  theme?: "default" | "test";
}

export function WorkflowShell({
  title,
  description,
  caseId,
  steps,
  currentStep,
  headerActions,
  children,
  theme = "default"
}: WorkflowShellProps) {
  return (
    <main className="workflow-app" data-theme={theme} data-current-step={currentStep}>
      <header className="workflow-shell__header">
        <div className="workflow-shell__identity">
          <a className="workflow-shell__home-link" href="/integration/cases">
            {workflowCopy.productName}
          </a>
          <h1>{title}</h1>
          {description ? <p>{description}</p> : null}
          {caseId ? <p className="workflow-shell__case-id">病例 ID：<code>{caseId}</code></p> : null}
        </div>
        {headerActions ? <div className="workflow-shell__actions">{headerActions}</div> : null}
      </header>

      <nav className="workflow-steps" aria-label="病例处理步骤">
        <ol>
          {steps.map((step, index) => {
            const copy = workflowCopy.steps[step.id];
            const isCurrent = step.id === currentStep;
            return (
              <li key={step.id} data-state={step.state}>
                <a
                  href={`#workflow-step-${step.id}`}
                  aria-current={isCurrent ? "step" : undefined}
                  aria-disabled={step.state === "blocked" ? true : undefined}
                >
                  <span className="workflow-step__index" aria-hidden="true">{index + 1}</span>
                  <span>
                    <strong>{copy.label}</strong>
                    <small>{copy.description}</small>
                  </span>
                </a>
              </li>
            );
          })}
        </ol>
      </nav>

      <div className="workflow-shell__content">{children}</div>
    </main>
  );
}

export interface WorkflowSectionProps {
  id: WorkflowStepId;
  title: string;
  description?: string;
  state: WorkflowStepState;
  actions?: ReactNode;
  children: ReactNode;
}

export function WorkflowSection({ id, title, description, state, actions, children }: WorkflowSectionProps) {
  return (
    <section
      id={`workflow-step-${id}`}
      className="workflow-section"
      data-step={id}
      data-state={state}
      aria-labelledby={`workflow-step-${id}-title`}
    >
      <div className="workflow-section__header">
        <div>
          <h2 id={`workflow-step-${id}-title`}>{title}</h2>
          {description ? <p>{description}</p> : null}
        </div>
        {actions ? <div className="workflow-section__actions">{actions}</div> : null}
      </div>
      <div className="workflow-section__body">{children}</div>
    </section>
  );
}

export function WorkflowNotice({
  tone = "info",
  children,
  live = false
}: {
  tone?: "info" | "success" | "warning" | "error";
  children: ReactNode;
  live?: boolean;
}) {
  return (
    <div
      className="workflow-notice"
      data-tone={tone}
      role={tone === "error" ? "alert" : undefined}
      aria-live={live && tone !== "error" ? "polite" : undefined}
    >
      {children}
    </div>
  );
}
