import React from "react";

import { operationStatusLabels } from "../../lib/api-v2/copy";
import type { OperationResponse } from "../../lib/api-v2/types";
import { WorkflowNotice } from "./workflow-shell";

export function WorkflowOperation({
  operation,
  polling,
  onStopPolling,
  onResumePolling
}: {
  operation: OperationResponse;
  polling?: boolean;
  onStopPolling?: () => void;
  onResumePolling?: () => void;
}) {
  const tone = operation.status === "failed" ? "error" : operation.status === "succeeded" ? "success" : "info";
  return (
    <WorkflowNotice tone={tone} live>
      <div className="workflow-operation" data-state={operation.status}>
        <div className="workflow-operation__summary">
          <strong>{operationStatusLabels[operation.status]}</strong>
          <span>{operation.progress.percent}%</span>
        </div>
        <progress max={100} value={operation.progress.percent} aria-label="工作流执行进度" />
        {operation.progress.current_item ? <p>当前处理：{operation.progress.current_item}</p> : null}
        {operation.failure ? (
          <p>{operation.failure.message}（{operation.failure.code}）</p>
        ) : null}
        {operation.status === "queued" || operation.status === "running" ? (
          <button
            className="workflow-button workflow-button--secondary"
            type="button"
            onClick={polling ? onStopPolling : onResumePolling}
          >
            {polling ? "停止自动轮询" : "继续自动轮询"}
          </button>
        ) : null}
      </div>
    </WorkflowNotice>
  );
}
