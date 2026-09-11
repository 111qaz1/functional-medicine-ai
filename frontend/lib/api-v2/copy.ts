import type { AnalysisStatus, CaseStatus, DraftGenerationStatus, OperationStatus } from "./types";

export const workflowCopy = {
  productName: "功能医学对接工作台",
  productSubtitle: "功能医学 AI 辅助系统",
  productDescription: "按病例完成资料提交、分析复核、草案审批与报告发布。",
  navigation: {
    workspace: "工作台",
    process: "分析流程",
    stepUnit: "步",
    clinicalNotice: "AI 结果仅供医生参考，最终方案以医生判断为准。"
  },
  entry: {
    title: "开始病例工作流",
    description: "创建新病例，或输入已知病例 ID 恢复处理。当前接口不提供病例列表。",
    createTitle: "创建病例",
    resumeTitle: "继续已有病例",
    createAction: "创建病例",
    resumeAction: "打开病例"
  },
  steps: {
    case: { label: "病例", description: "核对建档信息与临床摘要" },
    attachments: { label: "资料", description: "上传资料并启动综合分析" },
    review: { label: "复核", description: "提交医生差量修订" },
    draft: { label: "方案审核", description: "审核产品、剂量和安全提示" },
    report: { label: "最终报告", description: "编辑、批准并导出 PDF" }
  },
  common: {
    loading: "正在读取病例工作流…",
    retry: "重新加载",
    save: "保存更改",
    cancel: "取消",
    configurationError: "对接访问令牌尚未在 Next 服务端配置。",
    pollTimeout: "状态更新已暂停，后台任务仍在继续。请重新加载页面获取最新进度。"
  }
} as const;

export const workflowStepStateLabels = {
  complete: "已完成",
  current: "当前步骤",
  available: "可进入",
  blocked: "待解锁",
  error: "需处理"
} as const;

export const caseStatusLabels: Record<CaseStatus, string> = {
  intake: "待提交资料",
  files_received: "资料已接收",
  parsing_completed: "资料解析完成",
  ready_for_recommendation: "可开始分析",
  draft_generated: "草案已生成",
  under_review: "审核中",
  approved: "已发布"
};

export const analysisStatusLabels: Record<AnalysisStatus, string> = {
  queued: "分析已排队",
  preparing: "正在准备资料",
  analyzing_documents: "正在分析文档",
  synthesizing: "正在综合病例",
  validating: "正在校验结果",
  ready_for_review: "等待医生复核",
  reviewed: "医生已复核",
  stale: "分析已过期",
  failed: "分析失败"
};

export const draftGenerationStatusLabels: Record<DraftGenerationStatus, string> = {
  idle: "尚未生成草案",
  queued: "草案生成已排队",
  final_synthesizing: "正在综合复核结果",
  validating_support_needs: "正在校验支持需求",
  mapping_products: "正在匹配产品",
  checking_safety: "正在检查安全规则",
  generating_draft: "正在生成草案",
  ready: "草案已生成",
  failed: "草案生成失败"
};

export const operationStatusLabels: Record<OperationStatus, string> = {
  queued: "已排队",
  running: "执行中",
  succeeded: "已完成",
  failed: "执行失败"
};
