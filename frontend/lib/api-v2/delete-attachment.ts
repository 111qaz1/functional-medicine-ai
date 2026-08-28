import { WorkflowProblem } from "./gateway";
import type { CaseResponse, ProblemDetails } from "./types";

function isProblemDetails(value: unknown): value is ProblemDetails {
  if (!value || typeof value !== "object") return false;
  const problem = value as Record<string, unknown>;
  return (
    typeof problem.type === "string" &&
    typeof problem.title === "string" &&
    typeof problem.status === "number" &&
    typeof problem.detail === "string" &&
    typeof problem.instance === "string" &&
    typeof problem.code === "string" &&
    Array.isArray(problem.errors)
  );
}

function fallbackProblem(status: number): ProblemDetails {
  return {
    type: "urn:fm-ai:problem:gateway-response-invalid",
    title: "Gateway response invalid",
    status,
    detail: "删除病例资料时服务端返回了无法识别的响应，请重新加载后再试。",
    instance: "",
    code: "GATEWAY_RESPONSE_INVALID",
    errors: []
  };
}

export async function deleteCaseAttachment(caseId: string, attachmentId: string): Promise<CaseResponse> {
  const url = `/api/v2/cases/${encodeURIComponent(caseId)}/attachments/${encodeURIComponent(attachmentId)}`;
  const response = await fetch(url, {
    method: "DELETE",
    cache: "no-store",
    credentials: "include",
    headers: { Accept: "application/json, application/problem+json" }
  });

  if (!response.ok) {
    let payload: unknown;
    try {
      payload = await response.json();
    } catch {
      throw new WorkflowProblem(fallbackProblem(response.status));
    }
    throw new WorkflowProblem(isProblemDetails(payload) ? payload : fallbackProblem(response.status));
  }

  try {
    return await response.json() as CaseResponse;
  } catch {
    throw new WorkflowProblem(fallbackProblem(502));
  }
}
