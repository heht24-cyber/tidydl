import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tidydl.rules import category_for, load_rules


class TestRules(unittest.TestCase):
    def test_known(self):
        self.assertEqual(category_for(".jpg"), "Images")
        self.assertEqual(category_for(".PDF"), "Documents")
        self.assertEqual(category_for(".exe"), "Installers")
        self.assertEqual(category_for(".mkv"), "Videos")
        self.assertEqual(category_for(".flac"), "Audio")
        self.assertEqual(category_for(".zip"), "Archives")
        self.assertEqual(category_for(".ttf"), "Fonts")
        self.assertEqual(category_for(".py"), "Code")

    def test_unknown_and_empty(self):
        self.assertEqual(category_for(".xyzzy"), "Others")
        self.assertEqual(category_for(""), "Others")

    def test_custom_merge(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "r.json"
            f.write_text(json.dumps({"Images": ["avif"], "设计稿": ["psd", "sketch"]}), encoding="utf-8")
            rules = load_rules(f)
        self.assertIn("avif", rules["Images"])
        self.assertIn("jpg", rules["Images"])  # 内置仍在
        self.assertEqual(category_for(".psd", rules), "设计稿")
        self.assertEqual(category_for(".mp3", rules), "Audio")

    def test_bad_category_name(self):
        for bad in ("a/b", "..", ""):
            with tempfile.TemporaryDirectory() as td:
                f = Path(td) / "r.json"
                f.write_text(json.dumps({bad: ["x"]}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_rules(f)

    def test_bad_structure(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "r.json"
            f.write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_rules(f)
            f.write_text('{"Images": "jpg"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_rules(f)


if __name__ == "__main__":
    unittest.main()
