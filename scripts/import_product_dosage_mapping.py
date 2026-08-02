from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"main": MAIN_NS, "rel": REL_NS}


class DosageImportError(ValueError):
    pass


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def column_number(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference.upper())
    if not letters:
        raise DosageImportError(f"无法解析单元格坐标：{cell_reference}")
    value = 0
    for char in letters.group(0):
        value = value * 26 + ord(char) - ord("A") + 1
    return value


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return [
        "".join(node.text or "" for node in item.findall(".//main:t", NS))
        for item in root.findall("main:si", NS)
    ]


def worksheet_target(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relation_targets = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall(f"{{{REL_NS}}}Relationship")
        if item.attrib.get("Id") and item.attrib.get("Target")
    }
    for sheet in workbook.findall("main:sheets/main:sheet", NS):
        if sheet.attrib.get("name") != sheet_name:
            continue
        relation_id = sheet.attrib.get(f"{{{DOC_REL_NS}}}id")
        target = relation_targets.get(relation_id or "")
        if not target:
            break
        normalized = PurePosixPath("xl") / PurePosixPath(target.lstrip("/"))
        if str(normalized).startswith("xl/xl/"):
            normalized = PurePosixPath(str(normalized)[3:])
        return str(normalized)
    raise DosageImportError(f"工作簿中不存在工作表：{sheet_name}")


def read_sheet_cells(path: Path, sheet_name: str) -> tuple[dict[tuple[int, int], str], list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = read_shared_strings(archive)
        target = worksheet_target(archive, sheet_name)
        root = ET.fromstring(archive.read(target))
        cells: dict[tuple[int, int], str] = {}
        for cell in root.findall(".//main:sheetData/main:row/main:c", NS):
            reference = cell.attrib.get("r", "")
            row_match = re.search(r"\d+", reference)
            if not row_match:
                continue
            row = int(row_match.group(0))
            column = column_number(reference)
            cell_type = cell.attrib.get("t")
            if cell_type == "inlineStr":
                value = "".join(node.text or "" for node in cell.findall(".//main:t", NS))
            else:
                raw = cell.findtext("main:v", default="", namespaces=NS)
                if cell_type == "s":
                    try:
                        value = shared_strings[int(raw)]
                    except (ValueError, IndexError):
                        value = raw
                else:
                    value = raw
            value = str(value or "").strip()
            if value:
                cells[(row, column)] = value
        merged_ranges = [
            item.attrib["ref"]
            for item in root.findall(".//main:mergeCells/main:mergeCell", NS)
            if item.attrib.get("ref")
        ]
        return cells, merged_ranges


def _sequence_value(raw: str) -> int | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    integer = int(value)
    return integer if value == integer and 1 <= integer <= 31 else None


def extract_product_dosage_blocks(
    cells: dict[tuple[int, int], str],
    *,
    max_row: int,
    dosage_column: int = 10,
) -> dict[int, dict[str, object]]:
    starts = sorted(
        (row, sequence)
        for (row, column), raw in cells.items()
        if column == 1 and (sequence := _sequence_value(raw)) is not None
    )
    sequences = [sequence for _, sequence in starts]
    if sequences != list(range(1, 32)):
        raise DosageImportError(f"产品序号必须完整且依次为 1-31，实际为：{sequences}")

    result: dict[int, dict[str, object]] = {}
    for index, (start_row, sequence) in enumerate(starts):
        end_row = starts[index + 1][0] - 1 if index + 1 < len(starts) else max_row
        candidates = [
            (row, value)
            for (row, column), value in cells.items()
            if column == dosage_column and start_row <= row <= end_row and value.strip()
        ]
        distinct: dict[str, tuple[int, str]] = {}
        for row, value in candidates:
            identity = re.sub(r"\s+", "", value)
            distinct.setdefault(identity, (row, value.strip()))
        if not distinct:
            raise DosageImportError(
                f"产品 #{sequence}（行 {start_row}-{end_row}）缺少服用方式"
            )
        if len(distinct) > 1:
            conflict_rows = [row for row, _ in distinct.values()]
            raise DosageImportError(
                f"产品 #{sequence}（行 {start_row}-{end_row}）存在多个冲突剂量，来源行：{conflict_rows}"
            )
        source_row, dosage_text = next(iter(distinct.values()))
        result[sequence] = {
            "start_row": start_row,
            "end_row": end_row,
            "source_row": source_row,
            "dosage_text": dosage_text,
        }
    return result


def import_mapping(workbook_path: Path, output_path: Path, sheet_name: str) -> dict[str, object]:
    root = project_root()
    sys.path.insert(0, str(root / "backend"))
    from app.services.dosage_rules import DOSAGE_SOURCE_VERSION, parse_dosage_options

    existing = json.loads(output_path.read_text(encoding="utf-8-sig"))
    products = existing.get("products")
    if not isinstance(products, list) or len(products) != 31:
        raise DosageImportError("现有剂量映射必须包含 31 款产品")

    cells, merged_ranges = read_sheet_cells(workbook_path, sheet_name)
    max_row = max((row for row, _ in cells), default=0)
    blocks = extract_product_dosage_blocks(cells, max_row=max_row)

    imported_products: list[dict[str, object]] = []
    for current in products:
        sequence = int(current["source_sequence"])
        block = blocks[sequence]
        sku_id = str(current["sku_id"])
        updated = {
            key: value
            for key, value in current.items()
            if key not in {"dosage_text", "source_row", "source"}
        }
        updated.update(
            {
                "source_sheet": sheet_name,
                "source_product_row": block["start_row"],
                "source_product_end_row": block["end_row"],
                "source_dosage_row": block["source_row"],
                "dose_options": parse_dosage_options(
                    sku_id,
                    str(block["dosage_text"]),
                    source_row=int(block["source_row"]),
                    source_version=DOSAGE_SOURCE_VERSION,
                ),
            }
        )
        imported_products.append(updated)

    result: dict[str, object] = {
        "schema_version": 3,
        "source_file": workbook_path.name,
        "source_sheet": sheet_name,
        "source_version": DOSAGE_SOURCE_VERSION,
        "matching_note": "按产品序号区域读取服用方式列；物理合并单元格使用锚点值，缺失或冲突时导入失败。",
        "merged_ranges_checked": len(merged_ranges),
        "products": sorted(imported_products, key=lambda item: int(item["source_sequence"])),
    }
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="将客户确认的营养素剂量工作簿导入结构化映射")
    parser.add_argument("workbook", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=project_root() / "backend" / "app" / "data" / "product_dosage_mapping.json",
    )
    parser.add_argument("--sheet", default="单粒泡罩（中文）")
    args = parser.parse_args()
    result = import_mapping(args.workbook.resolve(), args.output.resolve(), args.sheet)
    print(f"已导入 {len(result['products'])} 款产品剂量到 {args.output.resolve()}")


if __name__ == "__main__":
    main()
