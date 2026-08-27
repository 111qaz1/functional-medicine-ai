"use client";

export interface OperationProgressState {
  placement: "upload" | "analysis" | "draft" | "report";
  title: string;
  stage: string;
  percent: number;
  status: "running" | "success" | "partial" | "error";
  detail?: string | null;
}

export function OperationProgress({ operation }: { operation: OperationProgressState | null }) {
  if (!operation) return null;
  const percent = Math.min(100, Math.max(0, operation.percent));
  return (
    <div className={`operation-progress operation-progress--${operation.status}`} role="status" aria-live="polite">
      <div className="operation-progress__head">
        <strong><span className="operation-progress__spinner" aria-hidden="true" />{operation.title}</strong>
        <span>{operation.status === "success" ? "完成" : operation.status === "partial" ? "部分完成" : operation.status === "error" ? "失败" : "处理中"}</span>
      </div>
      <div className="operation-progress__track" role="progressbar" aria-label={`${operation.title}进度`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{ width: `${percent}%` }} /></div>
      <p>{operation.stage}</p>
      {operation.detail ? <small>{operation.detail}</small> : null}
    </div>
  );
}
