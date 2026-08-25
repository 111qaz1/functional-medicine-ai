import React from "react";
import type { ReactNode } from "react";
import {
  ClipboardDocumentCheckIcon,
  ClipboardDocumentListIcon,
  DocumentArrowDownIcon,
  FolderOpenIcon,
  PencilSquareIcon
} from "@heroicons/react/24/outline";

import { workflowCopy, workflowStepStateLabels } from "../../lib/api-v2/copy";
import type { WorkflowStep, WorkflowStepId, WorkflowStepState } from "../../lib/api-v2/workflow-state";

const workflowStepIcons = {
  case: ClipboardDocumentListIcon,
  attachments: FolderOpenIcon,
  review: PencilSquareIcon,
  draft: ClipboardDocumentCheckIcon,
  report: DocumentArrowDownIcon
} as const;

export type WorkflowTheme = "paracelsus" | "test";

export interface WorkflowShellProps {
  title: string;
  description?: string;
  caseId?: string;
  steps: WorkflowStep[];
  currentStep: WorkflowStepId;
  onStepChange?: (step: WorkflowStepId) => void;
  headerActions?: ReactNode;
  brandSlot?: ReactNode;
  contextSlot?: ReactNode;
  children: ReactNode;
  theme?: WorkflowTheme;
}

export function WorkflowShell({
  title,
  description,
  caseId,
  steps,
  currentStep,
  onStepChange,
  headerActions,
  brandSlot,
  contextSlot,
  children,
  theme = "paracelsus"
}: WorkflowShellProps) {
  const currentStepIndex = Math.max(0, steps.findIndex((step) => step.id === currentStep));
  const currentStepCopy = workflowCopy.steps[currentStep];

  return (
    <div className="workflow-app" data-theme={theme} data-current-step={currentStep}>
      <aside className="workflow-shell__sidebar" aria-label="病例工作流导航">
        <div className="workflow-shell__brand">
          {brandSlot ?? (
            <a className="workflow-shell__home-link" href="/integration/cases">
              <strong>{workflowCopy.productName}</strong>
              <span>{workflowCopy.productSubtitle}</span>
            </a>
          )}
        </div>

        {contextSlot ? <div className="workflow-shell__context">{contextSlot}</div> : caseId ? (
          <div className="workflow-shell__context">
            <span>当前病例</span>
            <code>{caseId}</code>
          </div>
        ) : null}

        <p className="workflow-shell__nav-heading">{workflowCopy.navigation.process}</p>
        <nav className="integration-workflow-steps" aria-label="病例处理步骤">
          <ol>
            {steps.map((step) => {
              const copy = workflowCopy.steps[step.id];
              const StepIcon = workflowStepIcons[step.id];
              const isCurrent = step.id === currentStep;
              const isBlocked = step.state === "blocked";
              return (
                <li key={step.id} data-state={step.state}>
                  <button
                    type="button"
                    disabled={isBlocked}
                    onClick={() => onStepChange?.(step.id)}
                    aria-label={`${copy.label}：${workflowStepStateLabels[step.state]}`}
                    aria-current={isCurrent ? "step" : undefined}
                  >
                    <span className="workflow-step__index" aria-hidden="true"><StepIcon className="workflow-step__icon" /></span>
                    <span className="workflow-step__copy">
                      <strong>{copy.label}</strong>
                      <small>{workflowStepStateLabels[step.state]}</small>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>

        <p className="workflow-shell__disclaimer">{workflowCopy.navigation.clinicalNotice}</p>
      </aside>

      <main className="workflow-shell__workspace">
        <header className="workflow-shell__topbar">
          <div className="workflow-shell__breadcrumbs" aria-label="当前位置">
            <a href="/integration/cases">{workflowCopy.navigation.workspace}</a>
            {caseId ? <><span aria-hidden="true">/</span><span>{title}</span></> : null}
            <span aria-hidden="true">/</span>
            <strong>{currentStepCopy.label}</strong>
          </div>
          <div className="workflow-shell__topbar-meta">
            <span className="workflow-shell__step-count">
              第 <strong>{currentStepIndex + 1}</strong> / {steps.length} {workflowCopy.navigation.stepUnit}
            </span>
            {headerActions ? <div className="workflow-shell__actions">{headerActions}</div> : null}
          </div>
        </header>

        <div className="workflow-shell__page">
          <header className="workflow-shell__header">
            <div className="workflow-shell__identity">
              <h1>{title}</h1>
              {description ? <p>{description}</p> : null}
              {caseId ? <p className="workflow-shell__case-id">病例 ID：<code>{caseId}</code></p> : null}
            </div>
          </header>

          <div className="workflow-shell__content">{children}</div>
        </div>
      </main>
    </div>
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
