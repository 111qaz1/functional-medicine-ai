from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.providers.local import LocalObjectStore


class LocalObjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = LocalObjectStore(self.root)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_save_uses_bounded_generated_name_and_preserves_safe_suffix(self) -> None:
        original_name = f"{'病例总结' * 80}.TXT"
        content = "合成长文件名测试".encode("utf-8")

        stored_path = Path(self.store.save(original_name, content))

        self.assertTrue(stored_path.exists())
        self.assertEqual(stored_path.read_bytes(), content)
        self.assertRegex(stored_path.name, r"^[0-9a-f]{32}\.txt$")
        self.assertLess(len(stored_path.name.encode("utf-8")), 255)
        self.assertNotIn("病例总结", stored_path.name)

    def test_save_uses_bin_suffix_when_original_suffix_is_not_safe(self) -> None:
        stored_path = Path(self.store.save("病例总结.不安全扩展名", b"content"))

        self.assertRegex(stored_path.name, r"^[0-9a-f]{32}\.bin$")
        self.assertEqual(stored_path.read_bytes(), b"content")


if __name__ == "__main__":
    unittest.main()
