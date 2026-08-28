import { WorkflowProblem } from "./gateway";

export function isWorkflowProblem(error: unknown, ...codes: string[]): error is WorkflowProblem {
  return error instanceof WorkflowProblem && codes.includes(error.problem.code);
}

export function workflowErrorMessage(error: unknown): string {
  if (error instanceof WorkflowProblem) {
    if (error.problem.code === "FRONTEND_INTEGRATION_NOT_CONFIGURED") {
      return "对接访问令牌尚未在 Next 服务端配置，请联系系统管理员。";
    }
    if (error.problem.code === "AUTHENTICATION_REQUIRED") {
      return "对接访问令牌无效或已过期，请联系系统管理员更新服务端配置。";
    }
    if (error.problem.code === "CASE_ACCESS_DENIED") {
      return "当前对接身份无权访问该病例。";
    }
    return error.problem.detail || error.problem.title;
  }
  if (error instanceof Error && error.message) return error.message.trim();
  return "操作未完成，请稍后重试。";
}
