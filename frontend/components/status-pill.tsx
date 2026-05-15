import { CaseStatus } from "../lib/types";

const LABELS: Record<CaseStatus, string> = {
  intake: "待建档",
  files_received: "已收文件",
  parsing_completed: "已解析",
  ready_for_recommendation: "可生成草案",
  draft_generated: "草案待审",
  under_review: "审核中",
  approved: "已发布"
};

export function StatusPill({ status }: { status: CaseStatus }) {
  return <span className={`status-pill status-pill--${status}`}>{LABELS[status]}</span>;
}

