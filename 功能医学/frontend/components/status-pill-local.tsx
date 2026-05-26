import { CaseStatus } from "../lib/types";

const LABELS: Record<CaseStatus, string> = {
  intake: "待建档",
  files_received: "已收文件",
  parsing_completed: "待人工校对",
  ready_for_recommendation: "可生成草案",
  draft_generated: "已生成草案",
  under_review: "草案待审核",
  approved: "已发布"
};

export function StatusPillLocal({ status }: { status: CaseStatus }) {
  return <span className={`status-pill status-pill--${status}`}>{LABELS[status]}</span>;
}
