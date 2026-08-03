from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from app.domain.models import AbnormalFinding, ClinicalEvidenceClass, ConfirmedClinicalFinding


_GENETIC_FILE_TERMS = ("基因", "遗传", "gene", "genetic", "dna")
_GENETIC_CONTENT_TERMS = (
    "基因型",
    "基因位点",
    "遗传易感",
    "遗传风险",
    "风险基因",
    "等位基因",
    "突变型",
    "缺失型",
    "genotype",
    "allele",
    "polymorphism",
)
_RISK_LANGUAGE = ("风险", "易感", "可能增加", "倾向", "概率", "susceptibility", "risk")
_EXPOSURE_FILE_TERMS = ("毒性元素", "重金属", "微量元素", "元素分析", "环境暴露")
_EXPOSURE_CONTENT_TERMS = (
    "血镉",
    "尿镉",
    "血铅",
    "尿铅",
    "血汞",
    "尿汞",
    "血砷",
    "尿砷",
    "钡",
    "铝",
    "镉",
    "铅",
    "汞",
    "砷",
    "重金属",
    "毒性元素",
)
_FOLLOW_UP_RISK_TERMS = ("癌症风险", "恶性风险", "肿瘤风险", "患病风险")


def _normalized(*values: str | None) -> str:
    text = " ".join(str(value or "") for value in values)
    return unicodedata.normalize("NFKC", text).lower()


def is_genetic_risk_finding(finding: AbnormalFinding) -> bool:
    if str(finding.abnormal_flag or "").strip().lower() == "genetic_risk":
        return True
    filename = _normalized(finding.source_file_name)
    content = _normalized(
        finding.name,
        finding.result_text,
        finding.report_explanation,
        finding.neutral_interpretation,
        finding.interpretation,
        finding.source_text,
    )
    genetic_file = any(term in filename for term in _GENETIC_FILE_TERMS)
    genetic_content = any(term in content for term in _GENETIC_CONTENT_TERMS)
    risk_language = any(term in content for term in _RISK_LANGUAGE)
    return genetic_content or (genetic_file and risk_language)


def is_exposure_finding(finding: AbnormalFinding) -> bool:
    filename = _normalized(finding.source_file_name)
    content = _normalized(finding.name, finding.result_text, finding.source_text)
    return any(term in filename for term in _EXPOSURE_FILE_TERMS) or any(
        term in content for term in _EXPOSURE_CONTENT_TERMS
    )


def classify_finding_evidence(finding: AbnormalFinding) -> ClinicalEvidenceClass:
    content = _normalized(
        finding.name,
        finding.result_text,
        finding.report_explanation,
        finding.neutral_interpretation,
        finding.interpretation,
    )
    if is_genetic_risk_finding(finding):
        return ClinicalEvidenceClass.genetic_risk
    if any(term in content for term in _FOLLOW_UP_RISK_TERMS):
        return ClinicalEvidenceClass.follow_up_only
    if is_exposure_finding(finding):
        return ClinicalEvidenceClass.exposure
    if finding.raw_value or finding.reference_range or _has_objective_measurement(finding):
        return ClinicalEvidenceClass.lab_abnormal
    return ClinicalEvidenceClass.clinical_confirmed


def classify_confirmed_evidence(
    finding: ConfirmedClinicalFinding,
) -> ClinicalEvidenceClass:
    file_name = getattr(getattr(finding, "source_span", None), "file_name", "")
    content = _normalized(
        finding.finding_name,
        finding.finding_code,
        getattr(getattr(finding, "source_span", None), "snippet", ""),
    )
    normalized_file = _normalized(file_name)
    if any(term in content for term in _GENETIC_CONTENT_TERMS) or (
        any(term in normalized_file for term in _GENETIC_FILE_TERMS)
        and any(term in content for term in _RISK_LANGUAGE)
    ):
        return ClinicalEvidenceClass.genetic_risk
    if any(term in content for term in _FOLLOW_UP_RISK_TERMS):
        return ClinicalEvidenceClass.follow_up_only
    if any(term in normalized_file for term in _EXPOSURE_FILE_TERMS) or any(
        term in content for term in _EXPOSURE_CONTENT_TERMS
    ):
        return ClinicalEvidenceClass.exposure
    return finding.evidence_class


def strongest_evidence_class(
    values: Iterable[ClinicalEvidenceClass],
) -> ClinicalEvidenceClass:
    order = (
        ClinicalEvidenceClass.lab_abnormal,
        ClinicalEvidenceClass.clinical_confirmed,
        ClinicalEvidenceClass.symptom,
        ClinicalEvidenceClass.exposure,
        ClinicalEvidenceClass.genetic_risk,
        ClinicalEvidenceClass.follow_up_only,
    )
    value_set = set(values)
    return next((item for item in order if item in value_set), ClinicalEvidenceClass.symptom)


def system_evidence_score(findings: Iterable[AbnormalFinding]) -> float:
    weights = {
        ClinicalEvidenceClass.lab_abnormal: 28.0,
        ClinicalEvidenceClass.clinical_confirmed: 26.0,
        ClinicalEvidenceClass.symptom: 14.0,
        ClinicalEvidenceClass.exposure: 8.0,
        ClinicalEvidenceClass.genetic_risk: 3.0,
        ClinicalEvidenceClass.follow_up_only: 2.0,
    }
    unique: dict[tuple[str, str, str], float] = {}
    sources: set[str] = set()
    for finding in findings:
        evidence_class = classify_finding_evidence(finding)
        weight = (
            18.0
            if str(finding.abnormal_flag or "").lower() == "patient_reported"
            else weights[evidence_class]
        )
        key = (
            finding.source_file_id,
            re.sub(r"\s+", "", finding.name or "").lower(),
            re.sub(r"\s+", "", finding.result_text or "").lower(),
        )
        unique[key] = max(unique.get(key, 0.0), weight)
        if evidence_class not in {
            ClinicalEvidenceClass.genetic_risk,
            ClinicalEvidenceClass.follow_up_only,
        }:
            sources.add(finding.source_file_id)
    strongest = sorted(unique.values(), reverse=True)[:4]
    source_bonus = min(max(len(sources) - 1, 0) * 2.0, 6.0)
    return min(sum(strongest) + source_bonus, 82.0)


def is_underweight_finding(finding: AbnormalFinding) -> bool:
    if finding.finding_code == "underweight":
        return True
    text = _normalized(finding.name, finding.result_text, finding.source_text)
    if "体重过轻" in text or "体重偏低" in text or "低体重" in text:
        return True
    if "bmi" not in text:
        return False
    values = [float(value) for value in re.findall(r"(?<!\d)(\d{1,2}(?:\.\d+)?)(?!\d)", text)]
    return any(10.0 <= value < 18.5 for value in values)


def _has_objective_measurement(finding: AbnormalFinding) -> bool:
    text = " ".join(
        str(value or "")
        for value in (finding.raw_value, finding.result_text, finding.unit, finding.reference_range)
    )
    return bool(re.search(r"(?<!\w)[<>≤≥]?\s*-?\d+(?:\.\d+)?", text)) and bool(
        finding.unit or finding.reference_range or finding.raw_value
    )
