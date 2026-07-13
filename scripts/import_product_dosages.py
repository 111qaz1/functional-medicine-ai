from __future__ import annotations

import argparse
import json
import re
import unicodedata
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET


NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
BASELINE_TERMS = ("日常", "基础", "预防", "维护", "养护", "轻度", "成人基础")
DOCTOR_ONLY_TERMS = (
    "强化",
    "急性",
    "重度",
    "中度",
    "儿童",
    "备孕",
    "术后",
    "感染",
    "特殊",
    "应急",
    "缺乏纠正",
    "运动",
)
FORCED_MANUAL_SKUS = {
    "sku_dhea",
    "sku_vitamin_d3_k",
    "sku_female_hormone_balance",
}
DISABLED_SOURCE_SEQUENCES = {25, 26}


def _clean(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference or "")
    value = 0
    for char in letters.group(0) if letters else "":
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.itertext()) for node in root.findall(f"{{{NS_MAIN}}}si")]


def _sheet_path(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall(f"{{{NS_PACKAGE_REL}}}Relationship")
    }
    for sheet in workbook.findall(f".//{{{NS_MAIN}}}sheet"):
        if sheet.attrib.get("name") != sheet_name:
            continue
        target = targets[sheet.attrib[f"{{{NS_REL}}}id"]].lstrip("/")
        return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError(f"未找到工作表：{sheet_name}")


def _read_rows(path: Path, sheet_name: str) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        root = ET.fromstring(archive.read(_sheet_path(archive, sheet_name)))
        rows: list[list[str]] = []
        for row_node in root.findall(f".//{{{NS_MAIN}}}row"):
            values = [""] * 10
            for cell in row_node.findall(f"{{{NS_MAIN}}}c"):
                column = _column_index(cell.attrib.get("r", ""))
                if not 0 <= column < len(values):
                    continue
                cell_type = cell.attrib.get("t")
                if cell_type == "inlineStr":
                    value = "".join(cell.itertext())
                else:
                    value_node = cell.find(f"{{{NS_MAIN}}}v")
                    value = value_node.text if value_node is not None and value_node.text is not None else ""
                    if cell_type == "s" and value:
                        value = shared[int(value)]
                values[column] = _clean(value)
            rows.append(values)
        return rows


def _extract_scenarios(text: str) -> list[dict[str, str]]:
    scenarios: list[dict[str, str]] = []
    heading = ""
    for raw_line in str(text or "").splitlines():
        line = _clean(raw_line).strip(";；")
        if not line:
            continue
        if "粒" not in line or not re.search(r"每日|每周|隔日|单次|睡前", line):
            heading = line
            continue
        if "：" in line or ":" in line:
            prefix, candidate = re.split(r"[：:]", line, maxsplit=1)
            if re.search(r"每日|每周|隔日|单次|睡前", candidate) and "粒" in candidate:
                heading = prefix or heading
                line = candidate
        dosage = _conservative_dosage(line)
        if not dosage:
            continue
        context = f"{heading} {line}"
        doctor_only = any(term in context for term in DOCTOR_ONLY_TERMS)
        baseline = any(term in context for term in BASELINE_TERMS) and not doctor_only
        scenarios.append(
            {
                "label": heading or "未命名场景",
                "dosage": dosage,
                "access": "baseline_candidate" if baseline else "doctor_only",
            }
        )
    return scenarios


def _conservative_dosage(text: str) -> str:
    value = _clean(text)
    value = value.replace(",", "，").replace(";", "；")
    value = re.sub(r"每日\s*(\d+)\s*[-~至]\s*(\d+)\s*粒", r"每日 \1 粒", value)
    value = re.sub(r"每周\s*(\d+)\s*[-~至]\s*(\d+)\s*粒", r"每周 \1 粒", value)
    if re.search(r"隔日\s*1\s*粒\s*或\s*每周\s*3\s*粒", value):
        value = re.sub(r".*?隔日\s*1\s*粒\s*或\s*", "", value)
    value = re.sub(r"(每日|每周|隔日|单次)\s*(\d+)\s*粒", r"\1 \2 粒", value)
    value = re.sub(r"(早餐|午餐|晚餐)\s*(\d+)\s*粒", r"\1 \2 粒", value)
    value = re.split(r"[。；]", value, maxsplit=1)[0]
    value = re.split(
        r"，(?:长期|连续|提前|后续|症状|提供|辅助|维持|巩固|根据|补充|改善|稳定|维护|预防|快速|加速|避免|降低)",
        value,
        maxsplit=1,
    )[0]
    value = value.strip(" ，。；")
    return value + "。" if value else ""


def _product_rows(rows: list[list[str]]) -> dict[int, dict[str, str]]:
    products: dict[int, dict[str, str]] = {}
    for row in rows:
        sequence_text = _clean(row[0] if row else "")
        if not re.fullmatch(r"\d+", sequence_text):
            continue
        sequence = int(sequence_text)
        products[sequence] = {
            "short_name": _clean(row[1]),
            "product_name": _clean(row[2]),
            "source_dosage_text": _clean(row[9]),
        }
    return products


def build_matrix(*, workbook_path: Path, data_dir: Path, sheet_name: str) -> tuple[dict, dict]:
    report_catalog = json.loads((data_dir / "product_report_catalog.json").read_text(encoding="utf-8-sig"))
    product_catalog = json.loads((data_dir / "product_catalog.json").read_text(encoding="utf-8-sig"))
    catalog_by_sku = {item["sku_id"]: item for item in product_catalog}
    sku_by_sequence = {
        int(item["sequence"]): sku_id
        for sku_id, item in report_catalog.get("products", {}).items()
        if str(item.get("sequence", "")).isdigit()
    }
    source_products = _product_rows(_read_rows(workbook_path, sheet_name))

    products: list[dict] = []
    conflicts: list[dict] = []
    missing_source_dosage: list[str] = []
    unmapped_sequences: list[int] = []
    for sequence, source in sorted(source_products.items()):
        if sequence in DISABLED_SOURCE_SEQUENCES:
            continue
        sku_id = sku_by_sequence.get(sequence)
        if not sku_id or sku_id not in catalog_by_sku:
            unmapped_sequences.append(sequence)
            continue
        catalog = catalog_by_sku[sku_id]
        scenarios = _extract_scenarios(source["source_dosage_text"])
        baseline = next((item["dosage"] for item in scenarios if item["access"] == "baseline_candidate"), None)
        if not source["source_dosage_text"]:
            missing_source_dosage.append(sku_id)
        if baseline and _clean(baseline) != _clean(catalog["dosage_rule"]):
            conflicts.append(
                {
                    "sku_id": sku_id,
                    "catalog_fallback": catalog["dosage_rule"],
                    "source_baseline_candidate": baseline,
                }
            )
        products.append(
            {
                "sku_id": sku_id,
                "source_sequence": sequence,
                "source_product_name": source["short_name"] or source["product_name"],
                "review_status": "pending_review",
                "baseline_dosage_candidate": baseline,
                "catalog_fallback_dosage": catalog["dosage_rule"],
                "manual_confirmation_required": sku_id in FORCED_MANUAL_SKUS,
                "auto_draft_enabled": sku_id not in FORCED_MANUAL_SKUS,
                "doctor_only_options": [item for item in scenarios if item["access"] == "doctor_only"],
                "warnings": (
                    ["原始表格未在产品主行提供服用方式，未推断剂量，继续使用已审核目录兜底值。"]
                    if not source["source_dosage_text"]
                    else (["未识别到保守基础场景，继续使用已审核目录兜底值。"] if not baseline else [])
                ),
            }
        )

    matrix = {
        "version": f"{date.today().isoformat()}-candidate",
        "status": "pending_manual_review",
        "source_note": "由甲方提供的产品服用方式表派生；原始 Excel 不进入 Git。候选剂量经人工审核前不得覆盖产品目录。",
        "priority": ["doctor_case_edit", "reviewed_dosage_matrix", "product_catalog_fallback"],
        "disabled_source_sequences": sorted(DISABLED_SOURCE_SEQUENCES),
        "products": products,
    }
    report = {
        "generated_on": date.today().isoformat(),
        "source_product_count": len(source_products),
        "mapped_product_count": len(products),
        "pending_review_count": len(products),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "missing_source_dosage_skus": missing_source_dosage,
        "unmapped_source_sequences": unmapped_sequences,
        "disabled_source_products": [
            {"source_sequence": sequence, "source_name": source_products.get(sequence, {}).get("short_name", "")}
            for sequence in sorted(DISABLED_SOURCE_SEQUENCES)
        ],
        "activation_note": "逐项人工确认后，将对应 review_status 改为 reviewed；服务仅使用 reviewed 的基础剂量。",
    }
    return matrix, report


def main() -> None:
    parser = argparse.ArgumentParser(description="导入产品服用方式并生成待审核剂量矩阵。")
    parser.add_argument("xlsx", type=Path, help="本地产品说明 Excel；原始文件不会复制到仓库。")
    parser.add_argument("--sheet", default="单粒泡罩（中文）")
    parser.add_argument("--data-dir", type=Path, default=Path("backend/app/data"))
    args = parser.parse_args()
    matrix, report = build_matrix(workbook_path=args.xlsx, data_dir=args.data_dir, sheet_name=args.sheet)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    (args.data_dir / "product_dosage_matrix.json").write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.data_dir / "product_dosage_import_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"mapped": len(matrix["products"]), "conflicts": report["conflict_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
