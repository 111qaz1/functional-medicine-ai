from __future__ import annotations

import re
import unittest
from pathlib import Path


class RepositoryHygieneTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[2]
    SCANNED_SUFFIXES = {".py", ".json", ".md", ".ts", ".tsx", ".js"}
    SKIPPED_DIRS = {".git", ".runtime", "node_modules", "__pycache__", "dist", "build"}
    SKIPPED_GENERATED_FILES = {"knowledge_statements.json", "knowledge_statements_tfm2010_curated.json"}
    ALLOWED_PHONE_PLACEHOLDERS = {"010-00000000", "400-000-0000"}
    PHONE_PATTERN = re.compile(r"(?<!\d)(?:1[3-9]\d{9}|0\d{2,3}-\d{7,8}|400-\d{3}-\d{4})(?!\d)")
    FORBIDDEN_LITERALS = (
        "023" + "-65748318",
        "400" + "-123-4567",
        "\u738b\u5803",
        "\u65b9\u5803",
        "\u738b\u5f66\u5586",
        "\u7a46\u627f\u7a0b",
        "\u4f53\u91cd 49.4",
        "\u8eab\u9ad8 159.5",
        "\u4f53\u8d28\u6307\u6570 19.42",
        "\u6536\u7f29\u538b 99",
        "\u8212\u5f20\u538b 60",
        "\u8170\u56f4 65",
        "\u81c0\u56f4 88",
        "\u8170\u81c0\u6bd4 0.74",
        "NEUT " + "56.7",
        "LYM " + "37.7",
        "MONO " + "4.8",
        "NEUT# " + "2.40",
        "LYM# " + "1.60",
        "MONO# " + "0.20",
    )

    def test_source_files_do_not_contain_real_case_literals(self) -> None:
        offenders: list[str] = []
        for path in self._source_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for literal in self.FORBIDDEN_LITERALS:
                if literal in text:
                    offenders.append(f"{path.relative_to(self.ROOT)} contains forbidden literal {literal!r}")
            for match in self.PHONE_PATTERN.finditer(text):
                phone = match.group(0)
                if phone not in self.ALLOWED_PHONE_PLACEHOLDERS:
                    offenders.append(f"{path.relative_to(self.ROOT)} contains phone-like literal {phone!r}")

        self.assertFalse(offenders, "\n".join(offenders))

    def _source_files(self):
        for path in self.ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in self.SCANNED_SUFFIXES:
                continue
            if self.SKIPPED_DIRS & set(path.parts):
                continue
            if path.name in self.SKIPPED_GENERATED_FILES:
                continue
            yield path
