import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { createReviewDraft } from "../../lib/api-v2/review-diff";
import type { AnalysisResponse } from "../../lib/api-v2/types";
import { ReviewEditor } from "./review-editor";

const analysis = {
  revision: 3,
  abnormal_findings: [{
    id: "finding_1",
    name: "KAP轻链",
    result_text: "3.79 ↑",
    raw_value: "3.79",
    unit: "g/L",
    reference_range: "1.70–3.70",
    abnormal_flag: "high",
    source_file_id: "file_1",
    source_file_name: "fixture.pdf",
    source_page: 1,
    source_text: "KAP轻链 3.79 ↑",
    evidence_status: "verified_text",
    report_explanation: "报告解释内容",
    neutral_interpretation: "中性医学解释内容"
  }],
  current_supplements: [
    {
      id: "supplement_1",
      name: "维生素 D",
      source_file_ids: ["file_1"],
      source_file_names: ["fixture.pdf"],
      doctor_added: false
    },
    {
      id: "supplement_2",
      name: "鱼油",
      source_file_ids: [],
      source_file_names: [],
      doctor_added: true
    }
  ],
  food_sensitivity: {
    source_file_id: "file_1",
    source_file_name: "fixture.pdf",
    source_page: 2,
    items: [{
      id: "food_1",
      name: "酵母",
      raw_value: ">200",
      unit: "U/mL",
      abnormal_flag: "high",
      severity: "high",
      reported_grade: "3",
      reported_grade_meaning: "重度",
      reference_range: ">200 U/mL",
      grading_basis: "fixture grading",
      source_page: 2,
      source_text: "酵母 >200 U/mL",
      evidence_status: "verified_text"
    }],
    interpretations: [],
    valid: true,
    warning: "食敏结果需结合原报告复核"
  }
} as unknown as AnalysisResponse;

function renderEditor(sourceAnalysis: AnalysisResponse, value = createReviewDraft(sourceAnalysis)): string {
  return renderToStaticMarkup(
    <ReviewEditor
      analysis={sourceAnalysis}
      value={value}
      reviewerName="测试医生"
      sourceOptions={[{ id: "file_1", name: "fixture.pdf" }]}
      busy={false}
      conflict={false}
      onChange={() => undefined}
      onSubmit={() => undefined}
      onDiscardAndReload={() => undefined}
    />
  );
}

describe("ReviewEditor", () => {
  it("renders old-workbench finding and food-sensitivity summaries without technical copy", () => {
    const html = renderEditor(analysis);

    expect(html).toContain("报告解释内容");
    expect(html).toContain("中性医学解释内容");
    expect(html).toContain("3.79 g/L");
    expect(html).toContain("参考范围：1.70–3.70");
    expect(html).toContain("全部 1");
    expect(html).toContain("异常 1");
    expect(html).toContain("已核对 1");
    expect(html).toContain("酵母");
    expect(html).toContain("&gt;200 U/mL");
    expect(html).toContain("食敏结果需结合原报告复核");
    expect(html).toContain("来源：fixture.pdf");
    expect(html).toContain("医生补充");
    expect(html).toContain("保存校对并生成营养素草案");
    expect(html).not.toContain('value="报告解释内容"');
    expect(html).not.toContain("允许空差量");
    expect(html).not.toContain("v2 公开字段");
    expect(html).not.toContain("内部映射字段");
  });

  it("hides food sensitivity when analysis has no detected items", () => {
    const withoutFood = { ...analysis, food_sensitivity: null } as AnalysisResponse;
    const emptyFood = {
      ...analysis,
      food_sensitivity: { ...analysis.food_sensitivity!, items: [] }
    } as AnalysisResponse;

    for (const sourceAnalysis of [withoutFood, emptyFood]) {
      const html = renderEditor(sourceAnalysis);
      expect(html).not.toContain("慢性食物敏感");
      expect(html).not.toContain("新增食敏条目");
    }
  });

  it("keeps the detected food sensitivity section visible while the doctor removes the last item", () => {
    const value = { ...createReviewDraft(analysis), foodSensitivityItems: [] };
    const html = renderEditor(analysis, value);

    expect(html).toContain("慢性食物敏感");
    expect(html).toContain("新增食敏条目");
  });
});
