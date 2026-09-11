import type { DraftResponse } from "./types";

const REPORT_TITLE = "# 功能医学综合分析与首月干预方案";

const REPORT_SECTION_ORDER = [
  "核心结论与健康画像",
  "异常指标汇总",
  "慢性食物敏感检测结果",
  "功能医学系统失衡分析",
  "生活方式干预",
  "生活方式干预处方",
  "首月营养素干预方案",
  "现有补充剂调整建议",
  "需要补充确认",
  "待确认项",
  "方案总结",
  "复查与随访计划",
  "安全警示"
] as const;

const LIST_SECTIONS = new Set([
  "异常指标汇总",
  "慢性食物敏感检测结果",
  "生活方式干预",
  "生活方式干预处方",
  "首月营养素干预方案",
  "现有补充剂调整建议",
  "需要补充确认",
  "待确认项",
  "复查与随访计划",
  "安全警示"
]);

function chineseChapterNumber(value: number): string {
  const digits = "零一二三四五六七八九";
  if (value < 10) return digits[value];
  if (value < 20) return `十${value % 10 ? digits[value % 10] : ""}`;
  if (value < 100) {
    const tens = Math.floor(value / 10);
    const ones = value % 10;
    return `${digits[tens]}十${ones ? digits[ones] : ""}`;
  }
  return String(value);
}

export function buildPublishableReport(draft: DraftResponse): string {
  const sections = new Map(draft.report_sections.map((section) => [section.title, section.items]));
  const knownTitles = new Set<string>(REPORT_SECTION_ORDER);
  const orderedTitles = [
    ...REPORT_SECTION_ORDER,
    ...draft.report_sections.map((section) => section.title).filter((title) => !knownTitles.has(title))
  ];
  let chapterIndex = 0;
  const blocks = orderedTitles.flatMap((title) => {
    const items = (sections.get(title) ?? []).map((item) => item.trim()).filter(Boolean);
    if (!items.length) return [];
    const heading = `## ${chineseChapterNumber(++chapterIndex)}、${title}`;
    const rendered = items.map((item) => {
      if (item.startsWith("### ")) return item;
      return LIST_SECTIONS.has(title) ? `- ${item}` : item;
    });
    return [[heading, ...rendered].join("\n")];
  });
  return [REPORT_TITLE, ...blocks].join("\n\n");
}
