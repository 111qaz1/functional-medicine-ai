import sys
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.health_portrait import (
    build_core_health_portrait_result,
    validate_core_health_portrait,
)
from app.services.review_local import ReviewService


def _system(system_id: str, *finding_ids: str) -> SimpleNamespace:
    return SimpleNamespace(
        system_id=system_id,
        system_name=system_id,
        priority_level="中度关注",
        priority_score=50,
        summary=system_id,
        finding_ids=list(finding_ids),
    )


def _lab(
    finding_id: str,
    name: str,
    system_id: str,
    *,
    marker_code: str = "",
    value: str = "阳性",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=finding_id,
        name=name,
        system_ids=[system_id],
        evidence_class="lab_abnormal",
        abnormal_flag="high" if value != "阳性" else "positive",
        marker_code=marker_code,
        raw_value=value,
        result_text=value,
    )


def _valid_inputs() -> tuple[list[SimpleNamespace], list[SimpleNamespace]]:
    return (
        [
            _system("digestive_gut", "gut"),
            _system("liver_detox", "alt"),
            _system("immune_inflammation", "crp"),
        ],
        [
            _lab("gut", "肠道菌群失衡", "digestive_gut"),
            _lab("alt", "丙氨酸氨基转移酶", "liver_detox", marker_code="alt", value="112"),
            _lab("crp", "超敏C反应蛋白", "immune_inflammation", marker_code="hs_crp", value="12"),
        ],
    )


def test_ready_portrait_uses_whitelisted_chain_hubs_and_closed_loop() -> None:
    structured, abnormal = _valid_inputs()

    result = build_core_health_portrait_result(structured, abnormal_findings=abnormal)

    assert result.status == "ready"
    assert result.text.count("。") == 3
    assert "重要窗口期" not in result.text
    assert 1 <= len(result.decision.mechanism_chains) <= 3
    assert 1 <= len(result.decision.intervention_hubs) <= 2
    assert 3 <= len(result.decision.intervention_steps) <= 6
    assert result.text.endswith("功能稳态。")
    assert validate_core_health_portrait(result) == []


def test_food_sensitivity_alone_cannot_form_a_mainline_or_hub() -> None:
    result = build_core_health_portrait_result(
        [_system("digestive_gut", "food")],
        abnormal_findings=[
            _lab("food", "蛋清慢性食物过敏IgG", "digestive_gut"),
        ],
    )

    assert result.status == "degraded"
    assert result.decision.mechanism_chains == []
    assert result.decision.intervention_hubs == []
    assert "食物敏感结果强设干预枢纽" in result.text


def test_food_sensitivity_is_only_auxiliary_when_independent_objective_support_exists() -> None:
    structured, abnormal = _valid_inputs()
    structured[0].finding_ids.append("food")
    abnormal.append(_lab("food", "蛋清慢性食物过敏IgG", "digestive_gut"))

    result = build_core_health_portrait_result(structured, abnormal_findings=abnormal)

    assert result.status == "ready"
    assert any(
        "food" in chain.auxiliary_food_sensitivity_ids
        for chain in result.decision.mechanism_chains
    )
    assert all(
        "food" not in chain.supporting_finding_ids
        for chain in result.decision.mechanism_chains
    )


def test_p0_only_enters_referral_mode_without_failing_report() -> None:
    result = build_core_health_portrait_result(
        [_system("cardiovascular", "chest")],
        abnormal_findings=[
            _lab("chest", "持续性胸痛", "cardiovascular"),
        ],
        risk_notices=["持续性胸痛，需立即就医。"],
    )

    assert result.status == "referral_only"
    assert result.manual_review_required is True
    assert result.decision.intervention_hubs == []
    assert result.text.count("。") == 3
    assert "报告其余部分继续生成" in result.text


def test_non_p0_review_notice_does_not_block_ready_portrait() -> None:
    structured, abnormal = _valid_inputs()

    result = build_core_health_portrait_result(
        structured,
        abnormal_findings=abnormal,
        risk_notices=["当前用药信息需补充确认。"],
    )

    assert result.status == "ready"
    assert result.manual_review_required is True
    assert result.decision.risks.p0_referral == []
    assert result.decision.risks.review_required


def test_internal_or_registry_error_degrades_instead_of_raising() -> None:
    structured, abnormal = _valid_inputs()

    with patch("app.services.health_portrait._REGISTRY_PATH", object()):
        result = build_core_health_portrait_result(structured, abnormal_findings=abnormal)

    assert result.status == "degraded"
    assert result.manual_review_required is True
    assert result.validation_violations == ["portrait_internal_error:AttributeError"]
    assert "报告其余部分继续生成" in result.text


def test_trend_is_unknown_without_time_and_derived_with_comparable_timepoints() -> None:
    structured, abnormal = _valid_inputs()
    abnormal[1].observed_at = "2026-01-01T00:00:00Z"
    earlier_alt = _lab(
        "alt-old",
        "丙氨酸氨基转移酶",
        "liver_detox",
        marker_code="alt",
        value="80",
    )
    earlier_alt.observed_at = "2025-12-01T00:00:00Z"
    abnormal.append(earlier_alt)

    result = build_core_health_portrait_result(structured, abnormal_findings=abnormal)
    by_id = {item.finding_id: item for item in result.decision.findings}

    assert by_id["alt"].trend == "worsening"
    assert by_id["crp"].trend == "unknown"


def test_review_rerender_reuses_persisted_portrait_instead_of_recomputing() -> None:
    service = ReviewService.__new__(ReviewService)
    persisted_text = (
        "存在「肠道屏障受损—肝脏代谢负荷」一条主线的交叉联动。"
        "首月干预严格聚焦「肠道管理」核心，优先处理机制上游与交叉枢纽。"
        "通过「减少触发—改善消化—修复屏障」三步闭环，重建肠-肝轴功能稳态。"
    )
    draft = SimpleNamespace(
        core_health_portrait=SimpleNamespace(text=persisted_text),
        report_sections={"核心结论与健康画像": ["不应使用的旧文本。"]},
        structured_system_findings=[],
        key_lab_highlights=[],
        red_flags=[],
    )
    case = SimpleNamespace(confirmed_clinical_findings=[])

    report = service._ensure_core_health_portrait_section(
        "# 报告\n\n## 核心结论与健康画像\n旧文本。\n\n## 异常指标汇总\n现有内容。",
        draft,
        case,
    )

    assert persisted_text in report
    assert "不应使用的旧文本" not in report
    assert report.count("核心结论与健康画像") == 1
