"use client";

import Link from "next/link";
import { ChangeEvent, FormEvent, useEffect, useState } from "react";

import {
  approveDraft,
  createClinicianRuleFromCase,
  deleteClinicianRule,
  fetchCase,
  fetchCurrentUser,
  generateDraft,
  getPdfReportUrl,
  requestAssistantChat,
  reparseCaseFile,
  saveParsingReview,
  submitQuestionnaire,
  updateClinicalSummary,
  uploadClinicalSummaryImage,
  uploadQuestionnaireFile,
  updateClinicianRule,
  uploadCaseFile
} from "../lib/api";
import {
  CaseDetailResponse,
  CaseIndicator,
  ClinicianRule,
  DoctorAccount,
  ExtractedLabItem,
  ManualIndicatorInput,
  Questionnaire,
  RuleScope
} from "../lib/types";
import { SectionCard } from "./section-card";
import { StatusPillLocal } from "./status-pill-local";

function splitCsv(value: string) {
  return value
    .split(/[,\n，；;]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function stringifyLabItems(items: ExtractedLabItem[]) {
  return JSON.stringify(items, null, 2);
}

function joinEvidenceDetails(details: string[], ids: string[]) {
  return (details.length > 0 ? details : ids).join("；");
}

function formatSnippet(snippet: string) {
  const value = snippet.trim();
  if (!value) {
    return "未保留清晰原文片段，请以人工校对内容为准";
  }

  const allowedCharacters = value
    .split("")
    .filter(
      (char) =>
        /[\u4e00-\u9fffA-Za-z0-9]/.test(char) ||
        " .,:;/%+-()[]{}<>_=|#&*'\"，。；：、（）【】《》·".includes(char)
    ).length;
  const readableRatio = allowedCharacters / value.length;
  const longPackedToken = /[A-Za-z0-9]{18,}/.test(value) && !/\s/.test(value);

  if (value.includes("�") || readableRatio < 0.88 || longPackedToken) {
    return "原始片段识别不清，请以人工解析校对内容为准";
  }

  return value;
}

function formatIndicatorStatus(status: string) {
  const labels: Record<string, string> = {
    normal: "正常",
    attention: "需关注",
    positive: "阳性",
    info: "信息"
  };
  return labels[status] ?? status;
}

function getIndicatorRowClass(status: string) {
  if (status === "attention" || status === "positive") {
    return "indicator-row indicator-row--alert";
  }
  if (status === "normal") {
    return "indicator-row indicator-row--normal";
  }
  return "indicator-row";
}

function asReportItems(content?: string[] | string) {
  if (!content) {
    return [];
  }
  if (Array.isArray(content)) {
    return content.map((item) => String(item).trim()).filter(Boolean);
  }
  const text = String(content).trim();
  return text ? [text] : [];
}

function uniqueItems(items: string[]) {
  return Array.from(new Set(items.map((item) => item.trim()).filter(Boolean)));
}

function normalizeInlineSpacing(text: string) {
  return text
    .replace(/[ \t\f\v]+/g, " ")
    .replace(/\s*([，、。；：！？])\s*/g, "$1")
    .replace(/(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])/g, "")
    .replace(
      /(?<=[\u4e00-\u9fff])\s+(?=\d+(?:\.\d+)?(?:\s*[-~～]\s*\d+(?:\.\d+)?)?\s*(?:%|％|‰|℃|°|次|个|颗|粒|片|周|天|小时|分钟|秒))/g,
      ""
    )
    .replace(
      /(\d+(?:\.\d+)?(?:\s*[-~～]\s*\d+(?:\.\d+)?)?)\s+(%|％|‰|℃|°|次|个|颗|粒|片|周|天|小时|分钟|秒)/g,
      (_match, value, unit) => `${String(value).replace(/\s+/g, "")}${unit}`
    )
    .replace(/。+；/g, "；")
    .replace(/；+。/g, "；")
    .replace(/([，。；：！？、])\1+/g, "$1")
    .trim();
}

function normalizeNutritionItemPunctuation(text: string) {
  return `${text
    .trim()
    .replace(/[ ，。；]+$/g, "")
    .replace(/[。；]+(?=(?:目的|适用说明|注意\/禁忌)：)/g, "；")
    .replace(/[。；]+(?=与[\u4e00-\u9fffA-Za-z0-9])/g, "；")
    .replace(/。+；/g, "；")
    .replace(/；+。/g, "；")
    .replace(/([，。；：！？、])\1+/g, "$1")
    .replace(/[ ，。；]+$/g, "")}。`;
}

function normalizeReportLine(text: string) {
  const normalized = normalizeInlineSpacing(text);
  if (!normalized || normalized.startsWith("# ") || normalized.startsWith("## ")) {
    return normalized;
  }
  const prefix = normalized.startsWith("- ") ? "- " : "";
  let content = prefix ? normalized.slice(2).trim() : normalized;
  if (content.includes("目的：") || content.includes("适用说明：") || content.includes("注意/禁忌：")) {
    content = normalizeNutritionItemPunctuation(content);
  } else if (prefix && content && !/[。！？；）)]$/.test(content)) {
    content = `${content}。`;
  }
  return `${prefix}${content}`;
}

function collapseInlineSoftBreaks(text: string) {
  return normalizeInlineSpacing(
    text
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .replace(
        /(?<=[\u4e00-\u9fffA-Za-z0-9）)%％])\s*\n+\s*(?=[\u4e00-\u9fffA-Za-z0-9（(%％‰℃°])/g,
        ""
      )
      .replace(/\s*\n+\s*/g, " ")
  );
}

function normalizeCustomerVisibleReportText(reportText?: string | null) {
  const normalizedLines: string[] = [];
  for (const rawLine of String(reportText ?? "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")) {
    const line = normalizeReportLine(rawLine.trim());
    if (!line) {
      if (normalizedLines.length && normalizedLines[normalizedLines.length - 1] !== "") {
        normalizedLines.push("");
      }
      continue;
    }
    if (line.startsWith("# ") || line.startsWith("## ") || line.startsWith("- ")) {
      normalizedLines.push(line);
      continue;
    }
    if (normalizedLines.length && normalizedLines[normalizedLines.length - 1].startsWith("- ")) {
      normalizedLines[normalizedLines.length - 1] = normalizeReportLine(
        collapseInlineSoftBreaks(`${normalizedLines[normalizedLines.length - 1]}\n${line}`)
      );
      continue;
    }
    normalizedLines.push(line);
  }
  return normalizedLines.join("\n").trim();
}

function cleanCustomerText(item: string) {
  return collapseInlineSoftBreaks(item)
    .replace(/product:sku_[a-z0-9_]+/gi, "")
    .replace(/statement_[a-z0-9_]+/gi, "")
    .replaceAll("功能医学知识库（仅供参考）：", "")
    .replaceAll("功能医学知识库", "")
    .replaceAll("仅供参考", "")
    .replace(/\bRAG\b/gi, "")
    .replaceAll("当前草案", "当前方案")
    .replaceAll("候选推荐", "建议")
    .replaceAll("已审核知识命中", "本次资料提示")
    .replaceAll("人工复核", "顾问确认")
    .replace(/[ ，。；]+$/g, "")
    .trim();
}

function customerizeItems(content?: string[] | string) {
  return uniqueItems(asReportItems(content).map(cleanCustomerText).filter(Boolean));
}

function passesCustomerRagQuality(text: string) {
  const compact = text.replace(/\s+/g, "");
  const cjkCount = (compact.match(/[\u4e00-\u9fff]/g) ?? []).length;
  const latinCount = (compact.match(/[A-Za-z]/g) ?? []).length;
  if (/^[a-z]{3,}\b/.test(text.trim()) && cjkCount < 20) {
    return false;
  }
  if (/\b(potassium|chloride|bilirubin|alkaline|phosphatase|prostate specific antigen|palmitoleic|vaccenic)\b/i.test(text) && cjkCount < 20) {
    return false;
  }
  if (latinCount >= 30 && cjkCount < 8) {
    return false;
  }
  if (latinCount > Math.max(cjkCount * 4, 120) && cjkCount < 30) {
    return false;
  }
  return true;
}

function ragSectionItems(draft: NonNullable<CaseDetailResponse["latest_draft"]> | null | undefined, section: string) {
  if (!draft) {
    return [];
  }
  return customerizeItems(draft.report_sections[section]).filter(passesCustomerRagQuality);
}

function ragClauseForPurpose(raw: string, purpose: "health" | "indicator" | "lifestyle" | "followup") {
  const text = cleanCustomerText(raw);
  if (!text || !passesCustomerRagQuality(text)) {
    return "";
  }
  const lower = text.toLowerCase();
  const thyroid = ["甲状腺", "桥本", "hpt", "tsh", "ft3", "ft4", "tpo", "tgab", "抗体"].some((token) =>
    lower.includes(token)
  );
  const metabolic = ["血糖", "胰岛素", "代谢", "血脂", "胆固醇", "炎症", "crp"].some((token) =>
    lower.includes(token)
  );
  if (purpose === "health") {
    if (thyroid) {
      return "这也提示后续需要把甲状腺功能、抗体变化、症状表现、微量营养状态和整体代谢恢复放在同一张图里观察。";
    }
    if (metabolic) {
      return "这也提示后续需要把血糖血脂、炎症负担、睡眠压力和饮食活动放在同一张图里观察。";
    }
    return "这也提示后续需要把症状变化、关键指标、饮食作息和恢复状态放在同一张图里观察。";
  }
  if (purpose === "indicator") {
    if (thyroid) {
      return "从功能医学思路看，甲状腺相关异常不宜只看单项数值，建议结合HPT轴相关症状、抗体变化和甲状腺功能趋势一起评估。";
    }
    if (metabolic) {
      return "从功能医学思路看，这类异常建议结合代谢压力、炎症水平、饮食结构和复查趋势一起解释。";
    }
    return "从功能医学思路看，该指标更适合结合症状、相关指标和复查趋势综合判断。";
  }
  if (purpose === "lifestyle") {
    if (thyroid) {
      return "生活方式执行时可以把睡眠节律、压力恢复、抗炎饮食和规律活动作为同一组基础干预来推进。";
    }
    if (metabolic) {
      return "执行时建议把抗炎餐盘、餐后活动、睡眠修复和压力管理作为一组连续习惯来推进。";
    }
    return "执行时建议用可持续的小步调整观察身体反应，再逐步叠加下一阶段目标。";
  }
  if (thyroid) {
    return "复查时建议把甲状腺功能、抗体变化、症状和睡眠压力状态放在同一趋势里观察。";
  }
  if (metabolic) {
    return "复查时建议把代谢指标、炎症变化、体感精力和执行记录放在同一趋势里观察。";
  }
  return "复查时建议把关键指标、症状变化和执行记录放在同一趋势里观察。";
}

function firstRagClause(
  draft: NonNullable<CaseDetailResponse["latest_draft"]> | null | undefined,
  section: string,
  purpose: "health" | "indicator" | "lifestyle" | "followup"
) {
  for (const item of ragSectionItems(draft, section)) {
    const clause = ragClauseForPurpose(item, purpose);
    if (clause) {
      return clause;
    }
  }
  return "";
}

function appendClause(item: string, clause: string) {
  if (!clause || item.includes(clause)) {
    return item;
  }
  return `${item.replace(/[，。；;]+$/g, "")}。${clause}`;
}

function appendClauseToBestItem(items: string[], clause: string, preferredTokens: string[] = []) {
  if (!items.length || !clause) {
    return items;
  }
  const lowerClause = clause.toLowerCase();
  const tokens = preferredTokens.length
    ? preferredTokens
    : lowerClause.includes("甲状腺")
      ? ["甲状腺", "TSH", "FT3", "FT4", "TPO", "TGAb", "抗体"]
      : ["血糖", "胰岛素", "代谢", "炎症", "CRP", "胆固醇"];
  const index = Math.max(
    0,
    items.findIndex((item) => tokens.some((token) => item.toLowerCase().includes(token.toLowerCase())))
  );
  const nextItems = [...items];
  nextItems[index] = appendClause(nextItems[index], clause);
  return nextItems;
}

function publicSafetyWarnings(warnings: string[]) {
  return uniqueItems(
    warnings
      .map(cleanCustomerText)
      .filter((item) => item && !item.toLowerCase().includes("sku") && !item.includes("规格"))
  ).slice(0, 3);
}

function appendNutritionSafety(item: string, draft: NonNullable<CaseDetailResponse["latest_draft"]>) {
  if (item.includes("注意/禁忌")) {
    return item;
  }
  const matchedSku = draft.recommended_skus.find((sku) => item.includes(sku.display_name));
  if (!matchedSku) {
    return item;
  }
  const safety = publicSafetyWarnings(matchedSku.warnings);
  if (safety.length === 0) {
    return item;
  }
  return `${item.replace(/[ 。；]+$/g, "")}；注意/禁忌：${safety.join("；")}`;
}

function hasAny(text: string, tokens: string[]) {
  const normalized = text.toLowerCase();
  return tokens.some((token) => normalized.includes(token.toLowerCase()));
}

function abnormalIndicators(payload: CaseDetailResponse) {
  const seen = new Set<string>();
  return payload.display_indicators.filter((indicator) => {
    if (indicator.status !== "attention" && indicator.status !== "positive") {
      return false;
    }
    const key = `${indicator.indicator_name}|${indicator.result_text}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function indicatorFriendlyStatus(indicator: CaseIndicator) {
  const text = `${indicator.indicator_name} ${indicator.result_text} ${indicator.source_span?.snippet ?? ""}`;
  if (["↓", "偏低", "降低", "低于", "不足"].some((token) => text.includes(token))) {
    return "偏低";
  }
  if (["↑", "偏高", "升高", "增高", "高于"].some((token) => text.includes(token))) {
    return "偏高";
  }
  return indicator.status === "positive" ? "阳性/异常" : "需关注";
}

function indicatorExplanation(indicator: CaseIndicator) {
  const name = indicator.indicator_name.toLowerCase();
  const text = `${indicator.indicator_name} ${indicator.result_text} ${indicator.source_span?.snippet ?? ""}`.toLowerCase();
  if ((name.includes("25") && (name.includes("维生素d") || name.includes("vitamin d"))) || name.includes("羟维生素d")) {
    return "维生素D和免疫调节、骨骼健康、情绪与整体恢复有关，偏低时可把规律日晒、饮食来源和营养补充一起纳入计划。";
  }
  if (name.includes("体质指数") || name.includes("bmi")) {
    return "提示体重和体脂管理压力增加，建议重点观察腰围、餐盘结构、运动量和睡眠节律。";
  }
  if (name.includes("腰围")) {
    return "腰围偏高通常提示腹部脂肪压力增加，和血糖、血脂、脂肪肝及炎症负担都有关。";
  }
  if (name.includes("血压") || name.includes("收缩压") || name.includes("舒张压")) {
    return "血压偏离理想范围时，需要结合头晕、乏力、心悸、饮水量和用药情况一起判断。";
  }
  if (name.includes("尿素") || name.includes("bun") || name.includes("urea")) {
    return "尿素受蛋白摄入、水分状态和肾脏排泄影响，建议避免极端高蛋白或脱水，并结合肌酐、尿酸等指标一起看。";
  }
  if (["胆固醇", "甘油三酯", "低密度", "载脂蛋白", "ldl", "tg", "tc"].some((token) => name.includes(token))) {
    return "这类指标反映血脂和心血管代谢压力，建议和饮食油脂质量、精制碳水、运动量及腰围变化一起管理。";
  }
  if (["血糖", "葡萄糖", "糖化", "胰岛素", "hba1c", "glucose"].some((token) => name.includes(token))) {
    return "提示血糖稳定性需要关注，餐盘顺序、主食份量、饭后活动和睡眠都会影响后续变化。";
  }
  if (["crp", "炎症", "白细胞", "中性粒"].some((token) => name.includes(token))) {
    return "提示身体可能处在炎症或应激状态，近期可优先做好抗炎饮食、睡眠恢复和压力管理。";
  }
  if (["铁蛋白", "血清铁", "血红蛋白", "铁", "ferritin"].some((token) => name.includes(token))) {
    return "这类指标和铁储备、氧运输、疲劳及注意力有关，是否补铁需要结合完整铁代谢和医生评估。";
  }
  if (["甲状腺", "tsh", "ft3", "ft4", "tpo", "tgab"].some((token) => name.includes(token))) {
    return "甲状腺相关指标会影响代谢、体温、精力和情绪，建议同步关注压力、睡眠、硒锌铁状态和碘摄入是否合适。";
  }
  if (name.includes("尿酸")) {
    return "尿酸和嘌呤代谢、饮水量、酒精/含糖饮料、肾脏排泄有关，可先从饮食和水分管理入手。";
  }
  if (["alt", "ast", "ggt", "转氨酶", "胆红素"].some((token) => name.includes(token))) {
    return "这类指标和肝胆代谢、酒精、药物、脂肪肝及近期压力有关，建议减少肝脏负担并按需复查。";
  }
  if (name.includes("同型半胱氨酸") || name.includes("hcy")) {
    return "同型半胱氨酸偏高和B族维生素、甲基化及心血管管理有关，需要结合B12、叶酸和生活方式一起调整。";
  }
  if (name.includes("镁")) {
    return "镁与神经肌肉、睡眠和心律有关；异常时要同时确认肾功能、补剂使用和近期输液情况。";
  }
  if (text.includes("阳性")) {
    return "阳性结果提示需要结合症状和其他检查进一步判断，不建议只凭单项结果下结论。";
  }
  return "该指标已经偏离参考范围，建议结合症状、相关指标和复查趋势一起跟踪，暂不只凭单项结果下结论。";
}

function buildCustomerHealthPortrait(payload: CaseDetailResponse) {
  const draft = payload.latest_draft;
  const indicators = abnormalIndicators(payload).slice(0, 5).map((indicator) => indicator.indicator_name);
  const questionnaire = payload.case.questionnaire;
  const symptoms = questionnaire?.symptoms?.slice(0, 4) ?? [];
  const targets = (questionnaire?.chief_concerns?.length ? questionnaire.chief_concerns : questionnaire?.goals ?? []).slice(0, 3);
  const parts = [
    indicators.length
      ? `从这次报告看，当前更值得优先关注的是 ${indicators.join("、")}。`
      : "从这次报告看，暂时没有看到需要单独拎出来强调的异常指标。"
  ];
  if (symptoms.length) {
    parts.push(`结合您提到的 ${symptoms.join("、")}，建议把精力、代谢、睡眠和恢复状态放在一起看。`);
  }
  if (targets.length) {
    parts.push(`接下来的方案会围绕“${targets.join("、")}”这个目标，先从最容易执行的生活习惯开始。`);
  }
  parts.push("整体思路不是一次性做很多事，而是先把饮食结构、作息、压力和活动量这几个底盘稳定下来，再根据身体反应逐步调整营养素方案。");
  const ragClause = firstRagClause(draft, "RAG总体健康画像", "health");
  if (ragClause) {
    parts.push(ragClause);
  }
  return [parts.join("")];
}

function buildCustomerKeyIndicators(payload: CaseDetailResponse) {
  const draft = payload.latest_draft;
  const indicators = abnormalIndicators(payload);
  if (!indicators.length) {
    return ["本次未识别到需要重点展示的异常指标，后续以复查和症状变化继续跟踪即可。"];
  }
  const items = indicators.map(
    (indicator) =>
      `${indicator.indicator_name}：${indicator.result_text}（${indicatorFriendlyStatus(indicator)}）。说明：${indicatorExplanation(indicator)}`
  );
  const ragClause = firstRagClause(draft, "RAG异常指标解释", "indicator");
  return appendClauseToBestItem(items, ragClause);
}

function buildNutritionPlan(payload: CaseDetailResponse) {
  const draft = payload.latest_draft;
  if (!draft) {
    return [];
  }
  const fromSections = customerizeItems(draft.report_sections["个性化营养素方案"] ?? draft.report_sections["营养素推荐"]);
  if (fromSections.length) {
    return fromSections.map((item) => appendNutritionSafety(item, draft));
  }
  return draft.recommended_skus.map((sku) => {
    const safety = publicSafetyWarnings(sku.warnings);
    const safetySuffix = safety.length ? `；注意/禁忌：${safety.join("；")}` : "";
    return `${sku.display_name}：${sku.dosage}。目的：${cleanCustomerText(sku.reason)}${safetySuffix}`;
  });
}

function protocolLifestyleItems(payload: CaseDetailResponse) {
  const questionnaire = payload.case.questionnaire;
  const indicatorNames = abnormalIndicators(payload)
    .map((indicator) => indicator.indicator_name)
    .join(" ");
  const combined = [
    indicatorNames,
    questionnaire?.symptoms?.join(" ") ?? "",
    questionnaire?.known_conditions?.join(" ") ?? "",
    questionnaire?.goals?.join(" ") ?? ""
  ].join(" ");
  const items = [
    "饮食底盘：未来4-6周先按抗炎餐盘执行，每餐尽量做到半盘非淀粉蔬菜、1掌心优质蛋白、1拳头主食，烹调用橄榄油或蒸煮炖，减少油炸、甜食、酒精和深加工食品。",
    "执行节奏：首月不建议同时改太多，先选2-3条最容易做到的习惯连续执行2周，再逐步叠加下一步。"
  ];

  if (hasAny(combined, ["体质指数", "腰围", "血糖", "糖化", "胰岛素", "胆固醇", "甘油三酯", "脂肪肝", "代谢", "尿酸"])) {
    items.push("血糖与体重管理：吃饭顺序尽量按“蔬菜先、蛋白和脂肪其次、主食最后”，主食优先选择全谷物、豆类或薯类，饭后散步15-20分钟。");
    items.push("心血管代谢：参考地中海和DASH饮食思路，增加深海鱼或相应替代、坚果、豆类和高纤维蔬菜，减少加工肉、高盐外食和含糖饮料。");
  }
  if (hasAny(combined, ["甲状腺", "桥本", "tsh", "ft3", "ft4", "tpo", "tgab"])) {
    items.push("甲状腺友好：如有桥本、甲状腺抗体或甲减倾向，先避免自行高碘；十字花科蔬菜建议熟食，并观察麸质、乳制品是否会加重不适。");
  }
  if (hasAny(combined, ["维生素d", "25-", "免疫", "反复感染", "过敏"])) {
    items.push("免疫与恢复：白天规律户外光照，保证蛋白质和深色蔬菜摄入，减少过量糖分；如果需要补充维生素D，应结合复查结果调整。");
  }
  if (hasAny(combined, ["腹胀", "腹泻", "便秘", "肠", "食物敏感", "不耐受"])) {
    items.push("肠道修复：若腹胀、排便波动或食物敏感明显，可先做4周触发食物观察，减少超加工食品，同时记录饮食和症状变化。");
  }
  if (hasAny(combined, ["睡眠", "失眠", "疲劳", "焦虑", "压力", "情绪", "头痛"])) {
    items.push("睡眠修复：固定起床时间，晨起接触自然光15分钟；14点后减少咖啡因，睡前1小时减少屏幕和工作输入。");
    items.push("压力管理：每天安排2次5分钟呼吸练习或冥想，也可以用散步、哼唱、伸展来帮助身体从紧绷状态切换出来。");
  }
  if (hasAny(combined, ["alt", "ast", "ggt", "转氨酶", "胆红素", "脂肪肝", "酒精", "化学敏感"])) {
    items.push("肝胆代谢：至少4周减少酒精和高果糖加工食品，增加十字花科蔬菜、洋葱蒜类、足量饮水和膳食纤维，帮助身体降低代谢负担。");
  }
  if (questionnaire?.sitting_hours_per_day || questionnaire?.exercise_frequency) {
    items.push("运动处方：从可持续的活动开始，每天增加步行和拉伸；稳定后逐步过渡到每周150分钟中等强度有氧，加每周2次抗阻训练。");
  } else if (hasAny(combined, ["体质指数", "腰围", "血糖", "胆固醇", "疲劳", "线粒体"])) {
    items.push("活动恢复：先从饭后走路、每天累计8000步左右或低强度骑行开始，避免一上来就做高强度训练。");
  }
  if (hasAny(combined, ["尿素", "bun", "urea", "尿酸", "血压", "舒张压"])) {
    items.push("水分和安全：保持规律饮水，避免极端高蛋白、过度断食或突然大量运动；如有头晕、心悸、水肿或血压异常，优先联系医生。");
  }

  items.push("安全边界：如正在怀孕/哺乳、使用抗凝药、降糖药、甲状腺药或其他长期药物，任何饮食限制、禁食、排毒和补剂升级都应先让医生确认。");
  return uniqueItems(items);
}

function buildLifestyleFocus(payload: CaseDetailResponse) {
  const draft = payload.latest_draft;
  const draftItems = draft ? customerizeItems(draft.report_sections["生活方式干预重点"] ?? draft.lifestyle_actions) : [];
  const items = uniqueItems([...protocolLifestyleItems(payload), ...draftItems]).slice(0, 12);
  const ragClause = firstRagClause(draft, "RAG生活方式干预", "lifestyle");
  return appendClauseToBestItem(items, ragClause, ["睡眠", "压力", "饮食", "甲状腺", "运动", "执行"]);
}

function buildFollowUp(payload: CaseDetailResponse) {
  const draft = payload.latest_draft;
  if (!draft) {
    return [];
  }
  const items = [
    ...customerizeItems(draft.report_sections["功能医学检测建议"]),
    ...customerizeItems(draft.report_sections["随访计划"])
  ].slice(0, 6);
  const fallbackItems = [
    "建议2周内回访一次，重点看睡眠、精力、胃肠反应和方案执行难点。",
    "建议8-12周后结合本次异常指标做复查，用趋势来判断方案是否需要调整。"
  ];
  const baseItems = items.length ? items : fallbackItems;
  const ragClause = firstRagClause(draft, "RAG复查建议", "followup");
  return appendClauseToBestItem(baseItems, ragClause, ["甲状腺", "TSH", "FT3", "FT4", "抗体", "8-12", "复查"]);
}

function appendReportSection(lines: string[], title: string, items: string[]) {
  if (!items.length) {
    return;
  }
  lines.push(`## ${title}`);
  for (const item of items) {
    lines.push(`- ${item}`);
  }
  lines.push("");
}

function looksLikeInternalGeneratedReport(reportText?: string | null) {
  if (!reportText) {
    return false;
  }
  return ["## 病例摘要", "## 证据来源", "## 审核备注", "## 审计信息", "分析模式:", "模型版本:"].some((marker) =>
    reportText.includes(marker)
  );
}

function buildDraftReport(payload: CaseDetailResponse) {
  const draft = payload.latest_draft;
  if (!draft) {
    return "";
  }

  const lines = ["# 功能医学营养与生活方式建议", ""];
  appendReportSection(lines, "总体健康画像", buildCustomerHealthPortrait(payload));
  appendReportSection(lines, "关键指标", buildCustomerKeyIndicators(payload));
  appendReportSection(lines, "风险提示", customerizeItems(draft.report_sections["风险提示"] ?? draft.red_flags));
  appendReportSection(lines, "个性化营养素方案", buildNutritionPlan(payload));
  appendReportSection(lines, "生活方式干预重点", buildLifestyleFocus(payload));
  appendReportSection(lines, "复查与跟进建议", buildFollowUp(payload));
  appendReportSection(lines, "需要补充确认", customerizeItems(draft.report_sections["待确认项"] ?? draft.missing_info));
  appendReportSection(lines, "重要提醒", [
    "本报告用于健康管理和营养生活方式指导，不能替代医学诊断或治疗。",
    "如果出现胸痛、持续高热、黑便/便血、明显水肿、严重头晕或其他急性不适，请及时就医。"
  ]);

  return normalizeCustomerVisibleReportText(lines.join("\n"));
}

function buildPublishableReport(payload: CaseDetailResponse) {
  const storedReport = payload.review_decision?.publishable_report;
  if (storedReport && !looksLikeInternalGeneratedReport(storedReport)) {
    return normalizeCustomerVisibleReportText(storedReport);
  }
  return buildDraftReport(payload);
}

const MSQ_SECTIONS = [
  "头部",
  "眼部",
  "耳部",
  "鼻部",
  "口腔/咽喉",
  "皮肤",
  "心脏",
  "肺部",
  "消化道",
  "关节/肌肉",
  "体重",
  "能量/活动",
  "思维",
  "情绪",
  "其他"
] as const;

const DEFAULT_FORM: Questionnaire = {
  sex: "unknown",
  chief_concerns: [],
  symptoms: [],
  known_conditions: [],
  family_history: [],
  medications: [],
  allergies: [],
  food_sensitivities: [],
  emotional_state: [],
  goals: [],
  msq_system_scores: {}
};

type FileEditState = Record<string, { correctedText: string; missingFieldsText: string }>;
type ManualIndicatorEdit = {
  indicatorName: string;
  resultText: string;
  status: CaseIndicator["status"];
  evidenceText: string;
};
type AssistantChatTone = "default" | "success" | "warning";
type QuestionnaireImportStatus = "processing" | "completed" | "failed";
type QuestionnaireImportProgress = {
  filename: string;
  status: QuestionnaireImportStatus;
  message: string;
};
type ClinicalSummaryImageImportProgress = {
  filename: string;
  status: QuestionnaireImportStatus;
  message: string;
  extractedText?: string;
};
type AssistantChatMessage = {
  id: string;
  role: "assistant" | "doctor";
  text: string;
  tone: AssistantChatTone;
};

function createAssistantChatMessage(
  role: AssistantChatMessage["role"],
  text: string,
  tone: AssistantChatTone = "default"
): AssistantChatMessage {
  return {
    id: `${role}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role,
    text,
    tone
  };
}

function createManualIndicatorEdit(item?: CaseIndicator): ManualIndicatorEdit {
  return {
    indicatorName: item?.indicator_name ?? "",
    resultText: item?.result_text ?? "",
    status: item?.status ?? "attention",
    evidenceText: item?.source_span.snippet === "解析校对人工补录" ? "" : item?.source_span.snippet ?? ""
  };
}

function normalizeAssistantQuery(value: string) {
  return value.toLowerCase().replace(/\s+/g, "");
}

function includesAny(value: string, tokens: string[]) {
  return tokens.some((token) => value.includes(token));
}

function formatQuestionnaireImportStatus(status: QuestionnaireImportStatus) {
  const labels: Record<QuestionnaireImportStatus, string> = {
    processing: "识别中",
    completed: "识别完成",
    failed: "识别失败"
  };
  return labels[status];
}

function getQuestionnaireImportStatusClass(status: QuestionnaireImportStatus) {
  if (status === "completed") {
    return "normal";
  }
  if (status === "failed") {
    return "attention";
  }
  return "info";
}

export function CaseWorkbenchLocal({ caseId }: { caseId: string }) {
  const [payload, setPayload] = useState<CaseDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parsingHint, setParsingHint] = useState<string | null>(null);
  const [currentDoctor, setCurrentDoctor] = useState<DoctorAccount | null>(null);
  const [reviewerId, setReviewerId] = useState("reviewer-01");
  const [publishableSummary, setPublishableSummary] = useState("");
  const [questionnaire, setQuestionnaire] = useState<Questionnaire>(DEFAULT_FORM);
  const [questionnaireImportHint, setQuestionnaireImportHint] = useState<string | null>(null);
  const [questionnaireImportProgress, setQuestionnaireImportProgress] = useState<QuestionnaireImportProgress | null>(null);
  const [clinicalSummaryText, setClinicalSummaryText] = useState("");
  const [clinicalSummaryHint, setClinicalSummaryHint] = useState<string | null>(null);
  const [clinicalSummaryImageProgress, setClinicalSummaryImageProgress] = useState<ClinicalSummaryImageImportProgress[]>([]);
  const [labItemsEditor, setLabItemsEditor] = useState("[]");
  const [manualIndicatorEdits, setManualIndicatorEdits] = useState<ManualIndicatorEdit[]>([]);
  const [parsingMissingFieldsText, setParsingMissingFieldsText] = useState("");
  const [parsingReviewNotes, setParsingReviewNotes] = useState("");
  const [fileEdits, setFileEdits] = useState<FileEditState>({});
  const [assistantInstruction, setAssistantInstruction] = useState("");
  const [assistantRuleScope, setAssistantRuleScope] = useState<RuleScope>("public");
  const [assistantNotice, setAssistantNotice] = useState<string | null>(null);
  const [assistantInput, setAssistantInput] = useState("");
  const [assistantMessages, setAssistantMessages] = useState<AssistantChatMessage[]>([]);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantBusy, setAssistantBusy] = useState(false);

  function applyCasePayload(nextPayload: CaseDetailResponse) {
    setPayload(nextPayload);
    setQuestionnaire(nextPayload.case.questionnaire ?? DEFAULT_FORM);
    setClinicalSummaryText(nextPayload.case.clinical_summary_text ?? "");
    setLabItemsEditor(stringifyLabItems(nextPayload.case.extracted_lab_items));
    setManualIndicatorEdits((nextPayload.case.manual_indicators ?? []).map((item) => createManualIndicatorEdit(item)));
    setParsingMissingFieldsText(nextPayload.case.parsing_missing_fields.join(", "));
    setParsingReviewNotes(nextPayload.case.parsing_review_notes ?? "");
    setFileEdits(
      Object.fromEntries(
        nextPayload.case.files.map((file) => [
          file.id,
          {
            correctedText: file.corrected_text ?? file.raw_extracted_text ?? "",
            missingFieldsText: file.missing_fields.join(", ")
          }
        ])
      )
    );
    setPublishableSummary(buildPublishableReport(nextPayload));
    setAssistantMessages((current) =>
      current.length > 0
        ? current
        : [
            createAssistantChatMessage(
              "assistant",
              [
                `已接入病例 ${nextPayload.case.customer_name}。`,
                `当前识别到 ${nextPayload.display_indicators.length} 条关键指标，命中 ${nextPayload.matched_clinician_rules.length} 条医生规则。`,
                "你可以直接问我：总结当前病例、解释当前草案为什么这样推荐、为什么证据不足，或者把你的临床经验记录成后续可复用的医生规则。"
              ].join("\n"),
              "default"
            )
          ]
    );
  }

  async function refresh(options?: { showLoading?: boolean }) {
    const showLoading = options?.showLoading ?? false;
    try {
      if (showLoading) {
        setLoading(true);
      }
      const nextPayload = await fetchCase(caseId);
      applyCasePayload(nextPayload);
      setError(null);
      return;
      setQuestionnaire(nextPayload.case.questionnaire ?? DEFAULT_FORM);
      setClinicalSummaryText(nextPayload.case.clinical_summary_text ?? "");
      setLabItemsEditor(stringifyLabItems(nextPayload.case.extracted_lab_items));
      setParsingMissingFieldsText(nextPayload.case.parsing_missing_fields.join(", "));
      setParsingReviewNotes(nextPayload.case.parsing_review_notes ?? "");
      setFileEdits(
        Object.fromEntries(
          nextPayload.case.files.map((file) => [
            file.id,
            {
              correctedText: file.corrected_text ?? file.raw_extracted_text ?? "",
              missingFieldsText: file.missing_fields.join(", ")
            }
          ])
        )
      );
      setPublishableSummary(buildPublishableReport(nextPayload));
      setAssistantMessages((current) =>
        current.length > 0
          ? current
          : [
              createAssistantChatMessage(
                "assistant",
                [
                  `已接入病例 ${nextPayload.case.customer_name}。`,
                  `当前识别到 ${nextPayload.display_indicators.length} 条关键指标，命中 ${nextPayload.matched_clinician_rules.length} 条医生规则。`,
                  "你可以直接问我：总结当前病例、为什么这样推荐、当前命中哪些规则，或者直接发一条经验让我记住。"
                ].join("\n"),
                "default"
              )
            ]
      );
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载病例失败");
    } finally {
      if (showLoading) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    void refresh({ showLoading: true });
  }, [caseId]);

  useEffect(() => {
    async function loadCurrentDoctor() {
      try {
        const response = await fetchCurrentUser();
        setCurrentDoctor(response.doctor ?? null);
        if (response.doctor) {
          setReviewerId(response.doctor.display_name || response.doctor.username);
        }
      } catch {
        setCurrentDoctor(null);
      }
    }
    void loadCurrentDoctor();
  }, []);

  useEffect(() => {
    setAssistantMessages([]);
    setAssistantInput("");
    setAssistantOpen(false);
    setAssistantBusy(false);
    setQuestionnaireImportHint(null);
    setQuestionnaireImportProgress(null);
    setClinicalSummaryHint(null);
    setClinicalSummaryImageProgress([]);
  }, [caseId]);

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    try {
      setBusy(true);
      setParsingHint(`正在解析 ${file.name}，请稍候。图片或扫描件可能需要几十秒。`);
      await uploadCaseFile(caseId, file);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(false);
      setParsingHint(null);
      event.target.value = "";
    }
  }

  async function handleQuestionnaireSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      setBusy(true);
      await submitQuestionnaire(caseId, questionnaire);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "问卷提交失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleQuestionnaireUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    try {
      setBusy(true);
      setQuestionnaireImportHint(`正在识别 ${file.name}，请稍候。`);
      setQuestionnaireImportProgress({
        filename: file.name,
        status: "processing",
        message: "系统正在解析已填写的 MSQ 问卷，并准备回填到当前病例。"
      });
      await uploadQuestionnaireFile(caseId, file);
      await refresh();
      setQuestionnaireImportHint(`已从 ${file.name} 自动回填 MSQ 问卷，请人工核对后再生成最终报告。`);
      setQuestionnaireImportProgress({
        filename: file.name,
        status: "completed",
        message: "问卷识别完成，内容已自动回填到当前病例，请继续人工核对。"
      });
    } catch (err) {
      const messageText = err instanceof Error ? err.message : "MSQ 问卷导入失败";
      setError(messageText);
      setQuestionnaireImportHint(null);
      setQuestionnaireImportProgress({
        filename: file.name,
        status: "failed",
        message: `问卷识别失败：${messageText}`
      });
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  async function handleClinicalSummaryImageUpload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    if (files.length === 0) {
      return;
    }

    setClinicalSummaryImageProgress(
      files.map((file) => ({
        filename: file.name,
        status: "processing",
        message: "正在识别图片中的结论、小结和所需营养素，完成后会直接回填到上方病例总结。"
      }))
    );
    setClinicalSummaryHint(null);

    try {
      setBusy(true);
      for (const file of files) {
        try {
          const result = await uploadClinicalSummaryImage(caseId, file);
          applyCasePayload(result.case_detail);
          setError(null);
          setClinicalSummaryImageProgress((current) =>
            current.map((item) =>
              item.filename === file.name
                ? {
                    filename: file.name,
                    status: "completed",
                    message: "识别完成，已自动合并到当前的人工录入病例总结中。",
                    extractedText: result.extracted_text
                  }
                : item
            )
          );
          setClinicalSummaryHint(`已从 ${result.filename} 识别并回填到上方病例总结，你可以继续手动补充或直接生成草案。`);
        } catch (err) {
          const messageText = err instanceof Error ? err.message : "图片识别失败";
          setClinicalSummaryImageProgress((current) =>
            current.map((item) =>
              item.filename === file.name
                ? {
                    filename: file.name,
                    status: "failed",
                    message: `识别失败：${messageText}`
                  }
                : item
            )
          );
        }
      }
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  async function handleSaveClinicalSummary() {
    try {
      setBusy(true);
      const normalized = clinicalSummaryText.trim();
      await updateClinicalSummary(caseId, normalized);
      await refresh();
      setClinicalSummaryHint(
        normalized
          ? "病例总结诊断已保存，后续生成草案时会把这段评估结论一并纳入分析。"
          : "病例总结诊断已清空，后续将只基于报告、问卷和人工校对内容生成。"
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存病例总结诊断失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveParsingReview() {
    if (!payload) {
      return;
    }

    try {
      setBusy(true);
      const parsedItems = JSON.parse(labItemsEditor) as ExtractedLabItem[];
      if (!Array.isArray(parsedItems)) {
        throw new Error("指标校对内容必须是 JSON 数组。");
      }
      const hasIncompleteManualIndicator = manualIndicatorEdits.some(
        (item) => Boolean(item.indicatorName.trim()) !== Boolean(item.resultText.trim())
      );
      if (hasIncompleteManualIndicator) {
        throw new Error("人工补录关键指标需要同时填写指标名称和结果。");
      }
      const manualIndicators: ManualIndicatorInput[] = manualIndicatorEdits
        .filter((item) => item.indicatorName.trim() && item.resultText.trim())
        .map((item) => ({
          indicator_name: item.indicatorName.trim(),
          result_text: item.resultText.trim(),
          status: item.status,
          evidence_text: item.evidenceText.trim() || null
        }));
      await saveParsingReview(caseId, {
        reviewer_id: reviewerId,
        files: payload.case.files.map((file) => ({
          file_id: file.id,
          corrected_text: fileEdits[file.id]?.correctedText ?? file.corrected_text ?? file.raw_extracted_text ?? "",
          missing_fields: splitCsv(fileEdits[file.id]?.missingFieldsText ?? "")
        })),
        normalized_lab_items: parsedItems,
        manual_indicators: manualIndicators,
        missing_fields: splitCsv(parsingMissingFieldsText),
        review_notes: parsingReviewNotes || null
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "解析校对保存失败");
    } finally {
      setBusy(false);
    }
  }

  function addManualIndicator() {
    setManualIndicatorEdits((current) => [...current, createManualIndicatorEdit()]);
  }

  function updateManualIndicator(index: number, updates: Partial<ManualIndicatorEdit>) {
    setManualIndicatorEdits((current) =>
      current.map((item, itemIndex) => (itemIndex === index ? { ...item, ...updates } : item))
    );
  }

  function removeManualIndicator(index: number) {
    setManualIndicatorEdits((current) => current.filter((_, itemIndex) => itemIndex !== index));
  }

  async function handleGenerateDraft() {
    try {
      setBusy(true);
      await generateDraft(caseId, reviewerId);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成草案失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleReparseFile(fileId: string) {
    const targetFile = payload?.case.files.find((file) => file.id === fileId);
    try {
      setBusy(true);
      setParsingHint(`正在重新解析 ${targetFile?.filename ?? "当前文件"}，请稍候。`);
      await reparseCaseFile(caseId, fileId);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重新解析失败");
    } finally {
      setBusy(false);
      setParsingHint(null);
    }
  }

  async function handleApproveDraft() {
    if (!payload?.latest_draft) {
      return;
    }
    try {
      setBusy(true);
      const normalizedSummary = normalizeCustomerVisibleReportText(publishableSummary);
      setPublishableSummary(normalizedSummary);
      const review = await approveDraft(payload.latest_draft.id, reviewerId, normalizedSummary || undefined);
      window.open(getPdfReportUrl(review.draft_id), "_blank", "noopener,noreferrer");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "审核发布失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateAssistantRule() {
    if (!assistantInstruction.trim()) {
      setError("请先输入希望助手记住的医生经验。");
      return;
    }
    if (!currentDoctor) {
      setError("请先登录医生账号后再保存医生规则。公共工作台可以生成报告，但不能匿名写入规则库。");
      setAssistantNotice(null);
      return;
    }

    try {
      setBusy(true);
      const rule = await createClinicianRuleFromCase(caseId, assistantInstruction.trim(), assistantRuleScope);
      setAssistantInstruction("");
      setAssistantNotice(
        `已记录${rule.scope === "public" ? "公共" : "私人"}规则：${rule.title}。后续相似病例重新生成草案时会自动参考。`
      );
      setAssistantOpen(true);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存智慧助手规则失败");
      setAssistantNotice(null);
    } finally {
      setBusy(false);
    }
  }

  async function handleToggleAssistantRule(rule: ClinicianRule) {
    if (!currentDoctor) {
      setError("请先登录医生账号后再修改医生规则。");
      return;
    }
    try {
      setBusy(true);
      const updated = await updateClinicianRule({
        ...rule,
        enabled: !rule.enabled
      });
      setAssistantNotice(`${updated.title} 已${updated.enabled ? "启用" : "停用"}。`);
      setAssistantOpen(true);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新智慧助手规则失败");
      setAssistantNotice(null);
    } finally {
      setBusy(false);
    }
  }

  async function handleDeleteAssistantRule(rule: ClinicianRule) {
    if (!currentDoctor) {
      setError("请先登录医生账号后再删除医生规则。");
      return;
    }
    if (typeof window !== "undefined") {
      const confirmed = window.confirm(`确认删除智慧助手规则“${rule.title}”吗？删除后后续病例将不再参考它。`);
      if (!confirmed) {
        return;
      }
    }

    try {
      setBusy(true);
      await deleteClinicianRule(rule.id);
      setAssistantNotice(`已删除规则：${rule.title}`);
      setAssistantOpen(true);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除智慧助手规则失败");
      setAssistantNotice(null);
    } finally {
      setBusy(false);
    }
  }

  function appendAssistantMessage(
    role: AssistantChatMessage["role"],
    text: string,
    tone: AssistantChatTone = "default"
  ) {
    setAssistantMessages((current) => [...current, createAssistantChatMessage(role, text, tone)]);
  }

  function buildIndicatorReply() {
    const indicatorItems = payload?.display_indicators ?? [];
    if (indicatorItems.length === 0) {
      return "当前还没有可用的关键指标。你可以先上传并完成人工校对，我再帮你总结。";
    }

    const alerts = indicatorItems.filter((item) => item.status === "attention" || item.status === "positive");
    const normals = indicatorItems.filter((item) => item.status === "normal");
    const lines: string[] = [];

    if (alerts.length > 0) {
      lines.push(
        `当前优先关注 ${alerts.length} 项：${alerts
          .slice(0, 5)
          .map((item) => `${item.indicator_name} ${item.result_text}`)
          .join("；")}。`
      );
    }
    if (normals.length > 0) {
      lines.push(
        `另外已识别到 ${normals.length} 项相对平稳指标：${normals
          .slice(0, 4)
          .map((item) => `${item.indicator_name} ${item.result_text}`)
          .join("；")}。`
      );
    }
    if (alerts.length === 0) {
      lines.push("目前已识别的指标里，没有明显需要优先标红处理的项目。");
    }

    return lines.join("\n");
  }

  function buildDraftReasonReply() {
    if (!latestDraft) {
      return "当前还没有生成结构化草案。建议先完成解析校对，然后点击“生成结构化草案”。";
    }

    if (latestDraft.abstain_reason) {
      return `当前草案没有直接发布营养素组合，主要原因是：${latestDraft.abstain_reason}\n你可以继续补充问卷、确认用药过敏史，或在这里直接告诉我你希望沉淀成规则的医生经验。`;
    }

    if (latestDraft.recommended_skus.length === 0) {
      return "当前草案已经生成，但还没有形成明确的营养素组合。建议结合关键指标和问卷继续补充判断。";
    }

    return [
      "当前草案的推荐主要来自这几部分：已校对的关键指标、本地产品规则、已审核知识，以及命中的医生规则。",
      ...latestDraft.recommended_skus.slice(0, 3).map((item) => `${item.display_name}：${item.reason}`)
    ].join("\n");
  }

  function buildRuleReply() {
    if (matchedClinicianRules.length === 0) {
      return "当前病例还没有命中已沉淀的医生规则。你可以直接发一句经验给我，例如“以后遇到类似缺铁伴疲劳病例，优先加入植物多维矿和脂质体维C”。";
    }

    return [
      `当前命中 ${matchedClinicianRules.length} 条医生规则：`,
      ...matchedClinicianRules.slice(0, 4).map(
        (rule) =>
          `${rule.title}：${rule.enabled ? "已启用" : "已停用"}，${rule.action === "avoid" ? "谨慎/抑制" : "优先/增强"}，目标产品 ${rule.target_sku_ids.join("、")}`
      ),
      "如果你想集中修改这些规则，可以点上方“前往规则管理页”。"
    ].join("\n");
  }

  function buildCaseSummaryReply() {
    const completedQuestionnaire = payload?.case.questionnaire ? "已填写" : "未填写";
    return [
      `当前病例：${payload?.case.customer_name ?? "未命名病例"}。`,
      `已上传 ${payload?.case.files.length ?? 0} 份文件，解析校对${payload?.case.parsing_review_completed ? "已完成" : "尚未完成"}，问卷${completedQuestionnaire}。`,
      buildIndicatorReply(),
      latestDraft
        ? `草案状态是 ${latestDraft.status}${latestDraft.abstain_reason ? `，当前原因：${latestDraft.abstain_reason}` : "，可以继续审核或导出 PDF。"}`
        : "目前还没有生成草案。"
    ].join("\n");
  }

  function buildNextStepReply() {
    if ((payload?.case.files.length ?? 0) === 0) {
      return "下一步先上传体检报告或病例资料，系统解析后我再继续帮你整理。";
    }
    if (!payload?.case.parsing_review_completed) {
      return "下一步建议先完成“解析校对”。只有人工确认后的病例数据，后面的推荐和报告才会更稳定。";
    }
    if (!latestDraft) {
      return "下一步可以直接点击“生成结构化草案”。如果你希望先告诉我一条医生经验，我也可以先把它记成规则。";
    }
    if (!payload?.review_decision) {
      return "下一步建议审阅当前草案，必要时补充问卷或医生经验，再执行“审核并发布”。";
    }
    return "当前病例已经完成审核发布。接下来可以导出 PDF，或者把这次的临床经验继续沉淀成后续可复用的医生规则。";
  }

  async function rememberAssistantInstruction(message: string) {
    if (!currentDoctor) {
      const messageText = "请先登录医生账号后再保存医生规则。公共工作台可以生成报告，但不能匿名写入规则库。";
      setError(messageText);
      appendAssistantMessage("assistant", messageText, "warning");
      return;
    }
    try {
      setBusy(true);
      const rule = await createClinicianRuleFromCase(caseId, message, assistantRuleScope);
      setAssistantOpen(true);
      await refresh();
      appendAssistantMessage(
        "assistant",
        `我已经把这条经验记成${rule.scope === "public" ? "公共" : "私人"}规则“${rule.title}”。后续遇到相似病例、重新生成草案时，系统会自动把它纳入推荐加权。`,
        "success"
      );
    } catch (err) {
      const messageText = err instanceof Error ? err.message : "保存智慧助手规则失败";
      setError(messageText);
      appendAssistantMessage("assistant", `这条经验暂时没有记住，原因是：${messageText}`, "warning");
    } finally {
      setBusy(false);
    }
  }

  async function processAssistantMessage(message: string) {
    const trimmed = message.trim();
    if (!trimmed) {
      return;
    }

    appendAssistantMessage("doctor", trimmed, "default");
    setAssistantInput("");
    setAssistantOpen(true);

    const normalized = normalizeAssistantQuery(trimmed);
    const persistenceTerms = ["记住", "以后", "下次", "类似病例"];
    const actionTerms = ["优先加入", "优先推荐", "增加推荐", "加入推荐", "补进推荐", "移除", "排除", "不要推荐", "不推荐"];
    const questionTerms = ["为什么", "为何", "原因", "怎么", "如何", "查看", "总结", "哪些"];
    const shouldPersistRule =
      includesAny(normalized, persistenceTerms) ||
      (includesAny(normalized, actionTerms) && !includesAny(normalized, questionTerms));

    if (shouldPersistRule) {
      await rememberAssistantInstruction(trimmed);
      return;
    }

    if (includesAny(normalized, ["总结当前病例", "总结病例", "概括当前病例", "概括一下"])) {
      appendAssistantMessage("assistant", buildCaseSummaryReply(), "default");
      return;
    }

    if (includesAny(normalized, ["为什么这样推荐", "为什么推荐", "推荐原因", "草案为什么", "为何这样推荐"])) {
      appendAssistantMessage("assistant", buildDraftReasonReply(), "default");
      return;
    }

    if (includesAny(normalized, ["命中规则", "当前规则", "有哪些规则", "查看规则"])) {
      appendAssistantMessage("assistant", buildRuleReply(), "default");
      return;
    }

    if (includesAny(normalized, ["关键指标", "异常指标", "指标情况", "检验指标", "化验指标"])) {
      appendAssistantMessage("assistant", buildIndicatorReply(), "default");
      return;
    }

    if (includesAny(normalized, ["下一步", "后续怎么做", "接下来怎么做", "下一步做什么"])) {
      appendAssistantMessage("assistant", buildNextStepReply(), "default");
      return;
    }

    appendAssistantMessage(
      "assistant",
      [
        "我可以在这个病例里做三类事：",
        "1. 总结当前病例和关键指标。",
        "2. 解释当前草案为什么这样推荐或为什么证据不足。",
        "3. 把你的临床经验记成后续可复用的医生规则。",
        "你可以直接发一句话给我，例如：总结当前病例；为什么当前这样推荐；以后遇到类似病例优先加入某个营养素。"
      ].join("\n"),
      "default"
    );
  }

  function buildLocalAssistantFallback(message: string) {
    const normalized = normalizeAssistantQuery(message);

    if (includesAny(normalized, ["总结当前病例", "总结病例", "概括当前病例", "概括一下"])) {
      return buildCaseSummaryReply();
    }

    if (includesAny(normalized, ["为什么当前这样推荐", "为什么这样推荐", "推荐原因", "草案为什么", "为何这样推荐"])) {
      return buildDraftReasonReply();
    }

    if (includesAny(normalized, ["命中规则", "当前规则", "有哪些规则", "查看规则"])) {
      return buildRuleReply();
    }

    if (includesAny(normalized, ["关键指标", "异常指标", "指标情况", "检验指标", "化验指标"])) {
      return buildIndicatorReply();
    }

    if (includesAny(normalized, ["下一步", "后续怎么做", "接下来怎么做", "下一步做什么"])) {
      return buildNextStepReply();
    }

    const contextualParts = [buildIndicatorReply()];
    if (latestDraft) {
      contextualParts.push(buildDraftReasonReply());
    } else {
      contextualParts.push("当前还没有生成结构化草案。");
    }
    contextualParts.push("如果你想继续深入，可以直接问我：为什么当前这样推荐、当前哪些指标最关键、或者下一步怎么处理。");
    return contextualParts.join("\n");
  }

  async function processAssistantMessageRemote(message: string) {
    const trimmed = message.trim();
    if (!trimmed) {
      return;
    }

    const history = assistantMessages.map((item) => ({
      role: item.role,
      text: item.text
    }));

    appendAssistantMessage("doctor", trimmed, "default");
    setAssistantInput("");
    setAssistantOpen(true);

    const normalized = normalizeAssistantQuery(trimmed);
    const persistenceTerms = ["记住", "以后", "下次", "类似病例"];
    const actionTerms = ["优先加入", "优先推荐", "增加推荐", "加入推荐", "补进推荐", "移除", "排除", "不要推荐", "不推荐"];
    const questionTerms = ["为什么", "为何", "原因", "怎么", "如何", "查看", "总结", "哪些"];
    const shouldPersistRule =
      includesAny(normalized, persistenceTerms) ||
      (includesAny(normalized, actionTerms) && !includesAny(normalized, questionTerms));

    if (shouldPersistRule) {
      await rememberAssistantInstruction(trimmed);
      return;
    }

    try {
      setAssistantBusy(true);
      const response = await requestAssistantChat(caseId, trimmed, history);
      appendAssistantMessage("assistant", response.reply, "default");
      setAssistantNotice(
        response.mode === "llm"
          ? `本轮回复已通过大模型生成：${response.model_label}`
          : "当前未使用远程大模型，本轮已切换为本地解释模式。"
      );
    } catch (err) {
      const messageText = err instanceof Error ? err.message : "助手消息发送失败";
      setError(messageText);
      setAssistantNotice("助手接口调用失败，本轮已回退到页面内本地解释。");
      appendAssistantMessage("assistant", buildLocalAssistantFallback(trimmed), "warning");
    } finally {
      setAssistantBusy(false);
    }
  }

  async function handleAssistantSend() {
    await processAssistantMessageRemote(assistantInput);
  }

  if (loading) {
    return <p className="muted">正在加载病例工作台...</p>;
  }

  if (!payload) {
    return <p className="error-text">{error ?? "病例工作台加载失败，请返回列表后重试。"}</p>;
  }

  const caseRecord = payload.case;
  const displayIndicators = payload.display_indicators ?? [];
  const latestDraft = payload.latest_draft;
  const matchedClinicianRules = payload.matched_clinician_rules ?? [];
  const hasPendingFiles = caseRecord.files.some((file) => file.parse_status === "pending");
  const uploadStatusText =
    parsingHint ??
    (hasPendingFiles ? "病例文件仍在后台解析中，请稍候；完成后这里会自动显示更新后的解析状态。" : null);

  return (
    <div className="workbench">
      <div className="workbench__hero">
        <div>
          <Link href="/" className="back-link">
            返回工作台
          </Link>
          <h1>{caseRecord.customer_name}</h1>
          <p className="muted">
            病例 ID {caseRecord.id} · 顾问 {caseRecord.consultant_id ?? "未分配"} · 最近更新时间{" "}
            {new Date(caseRecord.updated_at).toLocaleString("zh-CN")}
          </p>
          <p className="muted">
            当前分析模式：
            {caseRecord.analysis_mode === "llm_primary" ? "大模型优先，本地知识辅助" : "本地知识优先"}
          </p>
        </div>
        <StatusPillLocal status={caseRecord.status} />
      </div>

      {error ? <p className="error-text">{error}</p> : null}

      <div className="workbench-grid">
        <SectionCard title="文件上传" subtitle="Document intake">
              <label className="upload-dropzone">
                <input
                  type="file"
                  accept=".pdf,.doc,.docx,.txt,.png,.jpg,.jpeg,.pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
                  onChange={handleUpload}
                  disabled={busy}
                />
                <span>上传 PDF / DOCX / PPTX / TXT / PNG / JPG</span>
                <small>本地版允许自动抽取不完整，后续由顾问人工校对。</small>
              </label>
          {uploadStatusText ? (
            <div className="info-note upload-status-note" aria-live="polite">
              <strong>解析中</strong>
              <p className="muted">{uploadStatusText}</p>
            </div>
          ) : null}
          <div className="stack">
            {caseRecord.files.map((file) => (
              <div key={file.id} className="file-row">
                <div>
                  <strong>{file.filename}</strong>
                  <p className="muted">
                    {file.content_type} · {Math.round(file.size_bytes / 1024)} KB · 解析状态 {file.parse_status} · 置信度{" "}
                    {file.parse_confidence ? Math.round(file.parse_confidence * 100) : 0}%
                  </p>
                </div>
              </div>
            ))}
            {caseRecord.files.length === 0 ? <p className="muted">还没有上传报告文件。</p> : null}
          </div>
          <div className="stack">
            <div className="section-divider">
              <strong>病例总结诊断</strong>
              <p className="muted">可在上传病例后，直接补充医生或评估师整理好的系统结论。</p>
            </div>
            <label className="field">
              <span>人工录入的评估结论 / 病例总结</span>
              <textarea
                rows={8}
                value={clinicalSummaryText}
                onChange={(event) => setClinicalSummaryText(event.target.value)}
                placeholder="可直接粘贴类似“脂肪酸代谢不佳、碳水化合物代谢不佳、细胞能量生成反应不佳”这类健康评估总结。保存后，系统会把这段内容一并用于支持方向分析、营养素候选排序和最终报告生成。"
              />
            </label>
            <label className="upload-dropzone upload-dropzone--compact">
              <input
                type="file"
                accept=".png,.jpg,.jpeg,.bmp,.gif,.tif,.tiff,.webp,image/png,image/jpeg,image/bmp,image/gif,image/tiff,image/webp"
                multiple
                onChange={handleClinicalSummaryImageUpload}
                disabled={busy}
              />
              <span>上传结论 / 小结 / 营养素推荐截图</span>
              <small>适合上传健康评估总结、所需营养素清单等图片，识别后会直接回填到上方病例总结。</small>
            </label>
            {clinicalSummaryImageProgress.length > 0 ? (
              <div className="stack">
                {clinicalSummaryImageProgress.map((item) => (
                  <div key={item.filename} className="file-row" aria-live="polite">
                    <div>
                      <strong>{item.filename}</strong>
                      <p className="muted">结论图片 · 识别状态 {formatQuestionnaireImportStatus(item.status)}</p>
                      <p className="muted">{item.message}</p>
                      {item.extractedText ? (
                        <p className="muted">
                          已识别文本预览：{item.extractedText.slice(0, 120)}
                          {item.extractedText.length > 120 ? "..." : ""}
                        </p>
                      ) : null}
                    </div>
                    <span className={`indicator-status indicator-status--${getQuestionnaireImportStatusClass(item.status)}`}>
                      {formatQuestionnaireImportStatus(item.status)}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
            <button className="primary-button" disabled={busy} onClick={() => void handleSaveClinicalSummary()}>
              保存总结诊断
            </button>
            <p className="muted">
              这一栏适合录入医生或评估师已经整理好的系统结论。即使没有完整问卷或原始化验指标，也可以作为报告生成的重要依据。
            </p>
            {clinicalSummaryHint ? <p className="info-note">{clinicalSummaryHint}</p> : null}
          </div>
        </SectionCard>

        <SectionCard title="MSQ 问卷" subtitle="MSQ-aligned intake">
          <div className="stack">
            <label className="upload-dropzone">
              <input
                type="file"
                accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                onChange={handleQuestionnaireUpload}
                disabled={busy}
              />
              <span>上传已填写的 MSQ 问卷</span>
              <small>支持已填写的 DOCX 问卷，识别后会自动带入当前病例分析流程。</small>
            </label>
            {questionnaireImportProgress ? (
              <div className="file-row" aria-live="polite">
                <div>
                  <strong>{questionnaireImportProgress.filename}</strong>
                  <p className="muted">MSQ DOCX · 问卷识别状态 {formatQuestionnaireImportStatus(questionnaireImportProgress.status)}</p>
                  <p className="muted">{questionnaireImportProgress.message}</p>
                </div>
                <span
                  className={`indicator-status indicator-status--${getQuestionnaireImportStatusClass(questionnaireImportProgress.status)}`}
                >
                  {formatQuestionnaireImportStatus(questionnaireImportProgress.status)}
                </span>
              </div>
            ) : null}
            {questionnaireImportHint ? (
              <div className="info-note" aria-live="polite">
                <strong>MSQ 导入</strong>
                <p className="muted">{questionnaireImportHint}</p>
              </div>
            ) : null}
          </div>
          <form className="stack" onSubmit={handleQuestionnaireSubmit}>
            <p className="muted">
              已按你放入的《MSQ--功能医学信息调查问卷》和甲方评估报告结构收口。问卷仍然是可选的，但补充后会让系统分析更完整。
            </p>

            <div className="grid-two">
              <label className="field">
                <span>年龄</span>
                <input
                  type="number"
                  value={questionnaire.age ?? ""}
                  onChange={(event) => setQuestionnaire((current) => ({ ...current, age: Number(event.target.value) || null }))}
                />
              </label>
              <label className="field">
                <span>性别</span>
                <select
                  value={questionnaire.sex}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, sex: event.target.value as Questionnaire["sex"] }))
                  }
                >
                  <option value="unknown">未填写</option>
                  <option value="female">女</option>
                  <option value="male">男</option>
                  <option value="other">其他</option>
                </select>
              </label>
            </div>

            <label className="field">
              <span>主要诉求</span>
              <input
                value={questionnaire.chief_concerns.join(", ")}
                onChange={(event) =>
                  setQuestionnaire((current) => ({ ...current, chief_concerns: splitCsv(event.target.value) }))
                }
                placeholder="例如：疲惫, 睡眠浅, 体能差"
              />
            </label>

            <label className="field">
              <span>主要症状</span>
              <input
                value={questionnaire.symptoms.join(", ")}
                onChange={(event) =>
                  setQuestionnaire((current) => ({ ...current, symptoms: splitCsv(event.target.value) }))
                }
                placeholder="例如：疲劳, 腹胀, 焦虑, 睡眠不深"
              />
            </label>

            <div className="grid-two">
              <label className="field">
                <span>既往诊断</span>
                <input
                  value={questionnaire.known_conditions.join(", ")}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, known_conditions: splitCsv(event.target.value) }))
                  }
                  placeholder="例如：桥本甲状腺炎, 甲减"
                />
              </label>
              <label className="field">
                <span>家族史</span>
                <input
                  value={questionnaire.family_history.join(", ")}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, family_history: splitCsv(event.target.value) }))
                  }
                  placeholder="例如：糖尿病, 甲状腺病, 心血管风险"
                />
              </label>
            </div>

            <div className="grid-two">
              <label className="field">
                <span>当前用药</span>
                <input
                  value={questionnaire.medications.join(", ")}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, medications: splitCsv(event.target.value) }))
                  }
                  placeholder="例如：优甲乐, 二甲双胍, warfarin"
                />
              </label>
              <label className="field">
                <span>过敏史</span>
                <input
                  value={questionnaire.allergies.join(", ")}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, allergies: splitCsv(event.target.value) }))
                  }
                  placeholder="例如：鱼, 大豆, 花生"
                />
              </label>
            </div>

            <div className="grid-two">
              <label className="field">
                <span>食物敏感/不耐受</span>
                <input
                  value={questionnaire.food_sensitivities.join(", ")}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, food_sensitivities: splitCsv(event.target.value) }))
                  }
                  placeholder="例如：麸质, 乳制品, 大豆"
                />
              </label>
              <label className="field">
                <span>健康目标</span>
                <input
                  value={questionnaire.goals.join(", ")}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, goals: splitCsv(event.target.value) }))
                  }
                  placeholder="例如：甲状腺支持, 肠道修复, 能量恢复"
                />
              </label>
            </div>

            <div className="grid-two">
              <label className="field">
                <span>工作/生活方式</span>
                <input
                  value={questionnaire.work_pattern ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, work_pattern: event.target.value || null }))
                  }
                  placeholder="例如：电脑工作 6-7h/日, 久坐"
                />
              </label>
              <label className="field">
                <span>每日久坐时长</span>
                <input
                  type="number"
                  step="0.5"
                  value={questionnaire.sitting_hours_per_day ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({
                      ...current,
                      sitting_hours_per_day: Number(event.target.value) || null
                    }))
                  }
                />
              </label>
            </div>

            <div className="grid-two">
              <label className="field">
                <span>外食频率</span>
                <input
                  value={questionnaire.dining_out_frequency ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, dining_out_frequency: event.target.value || null }))
                  }
                  placeholder="例如：5次/周"
                />
              </label>
              <label className="field">
                <span>压力等级</span>
                <select
                  value={questionnaire.stress_level ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({
                      ...current,
                      stress_level: (event.target.value || null) as Questionnaire["stress_level"]
                    }))
                  }
                >
                  <option value="">未填写</option>
                  <option value="low">低</option>
                  <option value="medium">中</option>
                  <option value="high">高</option>
                </select>
              </label>
            </div>

            <div className="grid-two">
              <label className="field">
                <span>睡眠时长</span>
                <input
                  type="number"
                  step="0.5"
                  value={questionnaire.sleep_hours ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, sleep_hours: Number(event.target.value) || null }))
                  }
                />
              </label>
              <label className="field">
                <span>睡眠质量</span>
                <input
                  value={questionnaire.sleep_quality ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, sleep_quality: event.target.value || null }))
                  }
                  placeholder="例如：浅睡, 多梦, 易醒"
                />
              </label>
            </div>

            <div className="grid-two">
              <label className="field">
                <span>运动频率</span>
                <input
                  value={questionnaire.exercise_frequency ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, exercise_frequency: event.target.value || null }))
                  }
                  placeholder="例如：无运动, 每周 3 次"
                />
              </label>
              <label className="field">
                <span>排便情况</span>
                <input
                  value={questionnaire.bowel_habits ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, bowel_habits: event.target.value || null }))
                  }
                  placeholder="例如：腹胀, 便秘/腹泻交替"
                />
              </label>
            </div>

            <div className="grid-two">
              <label className="field">
                <span>鱼/海鲜摄入</span>
                <input
                  value={questionnaire.seafood_intake_ratio ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, seafood_intake_ratio: event.target.value || null }))
                  }
                  placeholder="例如：5%, 很少"
                />
              </label>
              <label className="field">
                <span>红肉摄入</span>
                <input
                  value={questionnaire.red_meat_intake_ratio ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, red_meat_intake_ratio: event.target.value || null }))
                  }
                  placeholder="例如：15%, 偏高"
                />
              </label>
            </div>

            <div className="grid-two">
              <label className="field">
                <span>当前补充剂情况</span>
                <input
                  value={questionnaire.supplement_use ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, supplement_use: event.target.value || null }))
                  }
                  placeholder="例如：无补充剂, 已服用镁"
                />
              </label>
              <label className="field">
                <span>化学敏感/刺激暴露</span>
                <input
                  value={questionnaire.chemical_sensitivity ?? ""}
                  onChange={(event) =>
                    setQuestionnaire((current) => ({ ...current, chemical_sensitivity: event.target.value || null }))
                  }
                  placeholder="例如：香水, 杀虫剂"
                />
              </label>
            </div>

            <label className="field">
              <span>情绪状态</span>
              <input
                value={questionnaire.emotional_state.join(", ")}
                onChange={(event) =>
                  setQuestionnaire((current) => ({ ...current, emotional_state: splitCsv(event.target.value) }))
                }
                placeholder="例如：焦虑, 情绪低落, 自我怀疑"
              />
            </label>

            <label className="field checkbox">
              <input
                type="checkbox"
                checked={Boolean(questionnaire.pregnant_or_lactating)}
                onChange={(event) =>
                  setQuestionnaire((current) => ({ ...current, pregnant_or_lactating: event.target.checked }))
                }
              />
              <span>孕期或哺乳期</span>
            </label>

            <div className="stack">
              <strong>MSQ 系统负担评分</strong>
              <p className="muted">按系统负担程度填写 0-4 分：0 无，1 轻度，2 中度，3 中高，4 较重。</p>
              <div className="grid-two">
                {MSQ_SECTIONS.map((section) => (
                  <label key={section} className="field">
                    <span>{section}</span>
                    <select
                      value={String(questionnaire.msq_system_scores?.[section] ?? 0)}
                      onChange={(event) =>
                        setQuestionnaire((current) => ({
                          ...current,
                          msq_system_scores: {
                            ...current.msq_system_scores,
                            [section]: Number(event.target.value)
                          }
                        }))
                      }
                    >
                      <option value="0">0 无</option>
                      <option value="1">1 轻度</option>
                      <option value="2">2 中度</option>
                      <option value="3">3 中高</option>
                      <option value="4">4 较重</option>
                    </select>
                  </label>
                ))}
              </div>
            </div>

            <label className="field">
              <span>补充说明</span>
              <textarea
                rows={5}
                value={questionnaire.additional_notes ?? ""}
                onChange={(event) =>
                  setQuestionnaire((current) => ({ ...current, additional_notes: event.target.value || null }))
                }
                placeholder="可填写饮食偏好、月经情况、外食习惯、补充剂耐受、近期变化等"
              />
            </label>

            <button className="primary-button" disabled={busy}>
              保存问卷
            </button>
          </form>
        </SectionCard>

        <SectionCard title="解析校对" subtitle="Manual review">
          <div className="stack">
            <label className="field">
              <span>校对人</span>
              <input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} />
            </label>

            {caseRecord.files.map((file) => (
              <label key={file.id} className="field">
                <span>{file.filename} 文本校对</span>
                <textarea
                  rows={6}
                  value={fileEdits[file.id]?.correctedText ?? ""}
                  onChange={(event) =>
                    setFileEdits((current) => ({
                      ...current,
                      [file.id]: {
                        correctedText: event.target.value,
                        missingFieldsText: current[file.id]?.missingFieldsText ?? ""
                      }
                    }))
                  }
                />
                <input
                  value={fileEdits[file.id]?.missingFieldsText ?? ""}
                  onChange={(event) =>
                    setFileEdits((current) => ({
                      ...current,
                      [file.id]: {
                        correctedText: current[file.id]?.correctedText ?? "",
                        missingFieldsText: event.target.value
                      }
                    }))
                  }
                  placeholder="该文件仍缺失的信息，例如：参考区间, 页码"
                />
              </label>
            ))}

            <label className="field">
              <span>标准化指标 JSON</span>
              <textarea
                rows={12}
                value={labItemsEditor}
                onChange={(event) => setLabItemsEditor(event.target.value)}
              />
            </label>

            <div className="manual-indicator-panel">
              <div className="manual-indicator-panel__head">
                <div>
                  <strong>关键指标（人工补录）</strong>
                  <p className="muted">如果自动解析漏掉异常指标，可在这里录入；保存后会直接显示在下方关键指标并进入报告分析。</p>
                </div>
                <button type="button" className="secondary-button" disabled={busy} onClick={addManualIndicator}>
                  新增关键指标
                </button>
              </div>
              {manualIndicatorEdits.map((item, index) => (
                <div className="manual-indicator-card" key={`manual-indicator-${index}`}>
                  <div className="grid-two">
                    <label className="field">
                      <span>指标名称</span>
                      <input
                        value={item.indicatorName}
                        onChange={(event) => updateManualIndicator(index, { indicatorName: event.target.value })}
                        placeholder="例如：脂肪肝"
                      />
                    </label>
                    <label className="field">
                      <span>结果</span>
                      <input
                        value={item.resultText}
                        onChange={(event) => updateManualIndicator(index, { resultText: event.target.value })}
                        placeholder="例如：彩超提示脂肪肝"
                      />
                    </label>
                    <label className="field">
                      <span>状态</span>
                      <select
                        value={item.status}
                        onChange={(event) =>
                          updateManualIndicator(index, { status: event.target.value as CaseIndicator["status"] })
                        }
                      >
                        <option value="attention">需关注</option>
                        <option value="positive">阳性</option>
                        <option value="normal">正常</option>
                        <option value="info">信息</option>
                      </select>
                    </label>
                    <label className="field">
                      <span>证据片段或备注</span>
                      <input
                        value={item.evidenceText}
                        onChange={(event) => updateManualIndicator(index, { evidenceText: event.target.value })}
                        placeholder="例如：总检汇总分析第 3 项"
                      />
                    </label>
                  </div>
                  <button
                    type="button"
                    className="secondary-button secondary-button--danger"
                    disabled={busy}
                    onClick={() => removeManualIndicator(index)}
                  >
                    删除此项
                  </button>
                </div>
              ))}
              {manualIndicatorEdits.length === 0 ? <p className="muted">当前没有人工补录的关键指标。</p> : null}
            </div>

            <label className="field">
              <span>病例级缺失项</span>
              <input
                value={parsingMissingFieldsText}
                onChange={(event) => setParsingMissingFieldsText(event.target.value)}
                placeholder="例如：当前用药, 过敏史"
              />
            </label>

            <label className="field">
              <span>校对备注</span>
              <textarea
                rows={4}
                value={parsingReviewNotes}
                onChange={(event) => setParsingReviewNotes(event.target.value)}
                placeholder="可记录模板识别差异、页码说明或人工修正依据"
              />
            </label>

            <button className="primary-button" disabled={busy} onClick={() => void handleSaveParsingReview()}>
              保存解析校对
            </button>

            <p className="muted">
              当前校对状态：{caseRecord.parsing_review_completed ? "已完成" : "未完成"}。
              {caseRecord.parsing_reviewed_by ? ` 最近校对人 ${caseRecord.parsing_reviewed_by}` : ""}
            </p>
          </div>
        </SectionCard>

        <SectionCard title="关键指标" subtitle="Normalized labs">
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>指标</th>
                  <th>结果</th>
                  <th>状态</th>
                  <th>证据片段</th>
                </tr>
              </thead>
              <tbody>
                {displayIndicators.map((item, index) => (
                  <tr
                    key={`${item.category}-${item.indicator_name}-${index}`}
                    className={getIndicatorRowClass(item.status)}
                  >
                    <td>{item.indicator_name}</td>
                    <td>{item.result_text}</td>
                    <td>
                      <span className={`indicator-status indicator-status--${item.status}`}>
                        {formatIndicatorStatus(item.status)}
                      </span>
                    </td>
                    <td>{formatSnippet(item.source_span.snippet)}</td>
                  </tr>
                ))}
                {displayIndicators.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="muted">
                      上传并校对后，这里会显示标准化指标。
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </SectionCard>

        <SectionCard title="推荐草案与审核" subtitle="Draft and approval">
          <div className="stack">
            <button className="primary-button" disabled={busy} onClick={() => void handleGenerateDraft()}>
              生成结构化草案
            </button>

            {latestDraft ? (
              <>
                <div className="draft-meta">
                  <strong>草案状态：{latestDraft.status}</strong>
                  <span>置信度 {Math.round(latestDraft.confidence * 100)}%</span>
                </div>

                {latestDraft.abstain_reason ? <p className="warning-box">{latestDraft.abstain_reason}</p> : null}

                <div className="chip-list">
                  {latestDraft.red_flags.map((flag, index) => (
                    <span key={`red-flag-${index}-${flag}`} className="chip chip--danger">
                      {flag}
                    </span>
                  ))}
                  {latestDraft.missing_info.map((item, index) => (
                    <span key={`missing-${index}-${item}`} className="chip chip--muted">
                      {item}
                    </span>
                  ))}
                </div>

                <div className="stack">
                  <div>
                    <h3>结构化章节</h3>
                    {Object.entries(latestDraft.report_sections).map(([title, content]) => (
                      <div key={title} className="recommendation-row">
                        <div>
                          <strong>{title}</strong>
                          {Array.isArray(content) ? (
                            <ul className="flat-list">
                              {content.map((item, index) => (
                                <li key={`${title}-${index}-${item}`}>{item}</li>
                              ))}
                            </ul>
                          ) : (
                            <p>{String(content)}</p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div>
                    <h3>候选产品</h3>
                    <div className="stack">
                      {latestDraft.recommended_skus.map((sku) => (
                        <article key={sku.sku_id} className="recommendation-row">
                          <div>
                            <strong>{sku.display_name}</strong>
                            <p>{sku.reason}</p>
                            <p className="muted">
                              {sku.dosage} · 证据 {joinEvidenceDetails(sku.evidence_details, sku.evidence_ids) || "无"}
                            </p>
                            {publicSafetyWarnings(sku.warnings).length ? (
                              <p className="muted">注意/禁忌：{publicSafetyWarnings(sku.warnings).join("；")}</p>
                            ) : null}
                          </div>
                        </article>
                      ))}
                      {latestDraft.recommended_skus.length === 0 ? (
                        <p className="muted">当前草案没有给出营养素推荐。</p>
                      ) : null}
                    </div>
                  </div>
                </div>

                <label className="field">
                  <span>审核后发布内容</span>
                  <textarea
                    rows={14}
                    value={publishableSummary}
                    onChange={(event) => setPublishableSummary(event.target.value)}
                    placeholder="在这里编辑最终对外发布的结构化报告"
                  />
                </label>

                <button className="primary-button" disabled={busy} onClick={() => void handleApproveDraft()}>
                  审核并发布
                </button>

                {payload.review_decision?.pdf_report_path ? (
                  <div className="stack">
                    <a
                      className="primary-button"
                      href={getPdfReportUrl(payload.review_decision.draft_id)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      下载 PDF 报告
                    </a>
                    <p className="muted">PDF 已导出到：{payload.review_decision.pdf_report_path}</p>
                  </div>
                ) : null}
              </>
            ) : (
              <p className="muted">完成人工解析校对后即可生成结构化草案，问卷是可选的，但补充后会让推荐更完整。</p>
            )}
          </div>
        </SectionCard>

        <SectionCard title="审计日志" subtitle="Audit trail">
          <div className="audit-list">
            {payload.audit_logs.map((item) => (
              <div key={item.id} className="audit-row">
                <strong>{item.action}</strong>
                <p className="muted">
                  {item.actor_id} · {new Date(item.created_at).toLocaleString("zh-CN")}
                </p>
              </div>
            ))}
          </div>
        </SectionCard>
      </div>

      <div className={`assistant-widget${assistantOpen ? " assistant-widget--open" : ""}`}>
        {assistantOpen ? (
          <div className="assistant-widget__panel">
            <div className="assistant-widget__header">
              <div>
                <p className="assistant-widget__eyebrow">AI 助手</p>
                <h3>医生智慧助手</h3>
              </div>
              <button
                type="button"
                className="assistant-widget__close"
                onClick={() => setAssistantOpen(false)}
                aria-label="关闭智慧助手"
              >
                ×
              </button>
            </div>

            <div className="assistant-chat">
              <div className="inline-actions">
                <button
                  type="button"
                  className="assistant-chat__quick"
                  disabled={assistantBusy}
                  onClick={() => void processAssistantMessageRemote("总结当前病例")}
                >
                  总结当前病例
                </button>
                <button
                  type="button"
                  className="assistant-chat__quick"
                  disabled={assistantBusy}
                  onClick={() => void processAssistantMessageRemote("为什么当前这样推荐")}
                >
                  解释当前推荐
                </button>
                <button
                  type="button"
                  className="assistant-chat__quick"
                  disabled={assistantBusy}
                  onClick={() => void processAssistantMessageRemote("查看当前命中规则")}
                >
                  查看命中规则
                </button>
              </div>

              <div className="assistant-chat__messages">
                {assistantMessages.map((message) => (
                  <div
                    key={message.id}
                    className={`assistant-chat__message assistant-chat__message--${message.role} assistant-chat__message--${message.tone}`}
                  >
                    <div className="assistant-chat__meta">{message.role === "doctor" ? "医生" : "助手"}</div>
                    <div className="assistant-chat__bubble">
                      {message.text.split("\n").map((line, index) => (
                        <p key={`${message.id}-${index}`}>{line}</p>
                      ))}
                    </div>
                  </div>
                ))}
                {assistantBusy ? (
                  <div className="assistant-chat__message assistant-chat__message--assistant assistant-chat__message--default">
                    <div className="assistant-chat__meta">助手</div>
                    <div className="assistant-chat__bubble">
                      <p>正在结合当前病例、草案和规则整理回复...</p>
                    </div>
                  </div>
                ) : null}
              </div>

              <div className="assistant-chat__composer">
                <label className="field">
                  <span>给助手发消息</span>
                  <textarea
                    rows={4}
                    value={assistantInput}
                    onChange={(event) => setAssistantInput(event.target.value)}
                    placeholder="例如：总结当前病例；为什么当前这样推荐；以后遇到类似病例优先加入 rTG鱼油90%。"
                  />
                </label>
                <label className="field">
                  <span>如果这条消息要记成规则，保存到</span>
                  <select
                    value={assistantRuleScope}
                    onChange={(event) => setAssistantRuleScope(event.target.value as RuleScope)}
                    disabled={!currentDoctor}
                  >
                    <option value="public">公共规则库（所有医生可用）</option>
                    <option value="private">我的私人规则库</option>
                  </select>
                </label>
                {!currentDoctor ? (
                  <p className="muted">当前未登录医生账号，只能提问和生成报告，不能保存新的医生规则。</p>
                ) : null}
                <div className="inline-actions">
                  <button className="primary-button" disabled={busy || assistantBusy} onClick={() => void handleAssistantSend()}>
                    发送消息
                  </button>
                  <Link href="/assistant" className="secondary-button">
                    前往规则管理页
                  </Link>
                </div>
              </div>

              <div className="assistant-chat__rule-list">
                <strong>当前命中的医生规则</strong>
                {matchedClinicianRules.length === 0 ? (
                  <p className="muted">当前病例还没有命中的医生规则。你可以直接通过聊天发一条经验让我记住。</p>
                ) : (
                  matchedClinicianRules.map((rule) => (
                    <div key={rule.id} className="assistant-chat__rule-card">
                      <div>
                        <strong>{rule.title}</strong>
                          <p className="muted">
                            {rule.scope === "public" ? "公共规则" : "私人规则"} · {rule.enabled ? "已启用" : "已停用"} · {rule.action === "avoid" ? "谨慎/抑制" : "优先/增强"} ·
                            目标产品 {rule.target_sku_ids.join("、")}
                          </p>
                      </div>
                      <div className="case-row__actions">
                        <button
                          type="button"
                            className="secondary-button"
                            disabled={busy || !currentDoctor}
                            onClick={() => void handleToggleAssistantRule(rule)}
                          >
                          {rule.enabled ? "停用规则" : "启用规则"}
                        </button>
                        <button
                          type="button"
                            className="secondary-button secondary-button--danger"
                            disabled={busy || !currentDoctor}
                            onClick={() => void handleDeleteAssistantRule(rule)}
                          >
                          删除规则
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            <div className="assistant-widget__legacy stack">
              {assistantNotice ? (
                <div className="info-note">
                  <p>{assistantNotice}</p>
                </div>
              ) : null}

              <div className="inline-actions">
                <Link href="/assistant" className="secondary-button">
                  前往规则管理页
                </Link>
              </div>

              <div className="info-note">
                <p>
                  这里不再单独占用工作台版面。需要时点开即可，给出当前病例的经验指令，让系统把它沉淀成后续可复用的医生规则。
                </p>
                <p>例如：以后遇到这种缺铁伴疲劳的病例，把植物多维矿和脂质体维C优先放进推荐。</p>
              </div>

                <label className="field">
                  <span>医生经验指令</span>
                  <textarea
                    rows={4}
                    value={assistantInstruction}
                    onChange={(event) => setAssistantInstruction(event.target.value)}
                    placeholder="例如：以后遇到类似高血脂病例，优先加入 rTG鱼油90%，并把心血管支持作为第二优先级。"
                  />
                </label>
                <label className="field">
                  <span>规则保存位置</span>
                  <select
                    value={assistantRuleScope}
                    onChange={(event) => setAssistantRuleScope(event.target.value as RuleScope)}
                    disabled={!currentDoctor}
                  >
                    <option value="public">公共规则库（所有医生可用）</option>
                    <option value="private">我的私人规则库</option>
                  </select>
                </label>
                {!currentDoctor ? (
                  <p className="muted">当前未登录医生账号，只能提问和生成报告，不能保存新的医生规则。</p>
                ) : null}

              <button className="primary-button" disabled={busy || !currentDoctor} onClick={() => void handleCreateAssistantRule()}>
                让助手记住这条经验
              </button>

              <div className="stack">
                <strong>当前命中的医生规则</strong>
                {matchedClinicianRules.length === 0 ? (
                  <p className="muted">当前病例还没有命中的医生智慧规则。你可以先记录一条经验。</p>
                ) : (
                  matchedClinicianRules.map((rule) => (
                    <div key={rule.id} className="recommendation-row">
                      <div>
                        <strong>{rule.title}</strong>
                        <p className="muted">
                          {rule.scope === "public" ? "公共规则" : "私人规则"} · {rule.enabled ? "已启用" : "已停用"} · {rule.action === "avoid" ? "谨慎/抑制" : "优先/增强"} ·
                          目标产品 {rule.target_sku_ids.join("、")}
                        </p>
                        <p>{rule.instruction_text}</p>
                        {rule.notes ? <p className="muted">{rule.notes}</p> : null}
                        {rule.trigger_marker_rules.length > 0 ? (
                          <p className="muted">触发指标：{rule.trigger_marker_rules.join("；")}</p>
                        ) : null}
                        {rule.trigger_support_profiles.length > 0 ? (
                          <p className="muted">系统信号：{rule.trigger_support_profiles.join("、")}</p>
                        ) : null}
                      </div>
                      <div className="case-row__actions">
                        <button
                          type="button"
                          className="secondary-button"
                          disabled={busy || !currentDoctor}
                          onClick={() => void handleToggleAssistantRule(rule)}
                        >
                          {rule.enabled ? "停用规则" : "启用规则"}
                        </button>
                        <button
                          type="button"
                          className="secondary-button secondary-button--danger"
                          disabled={busy || !currentDoctor}
                          onClick={() => void handleDeleteAssistantRule(rule)}
                        >
                          删除规则
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        ) : null}

        <button
          type="button"
          className="assistant-widget__launcher"
          onClick={() => setAssistantOpen((current) => !current)}
          aria-expanded={assistantOpen}
          aria-label={assistantOpen ? "收起智慧助手" : "打开智慧助手"}
        >
          <span className="assistant-widget__launcher-label">AI</span>
          <strong>助手</strong>
          <small>{matchedClinicianRules.length > 0 ? `${matchedClinicianRules.length} 条规则` : "点此打开"}</small>
        </button>
      </div>
    </div>
  );
}
