import contextlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tidydl.cli import main


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def touch(self, name: str, age_minutes: float = 100) -> Path:
        p = self.td / name
        p.write_text("内容", encoding="utf-8")
        old = time.time() - age_minutes * 60
        os.utime(p, (old, old))
        return p

    def run_main(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()


class TestCli(Base):
    def test_dry_run_is_default(self):
        self.touch("photo.jpg")
        self.touch("setup.exe")
        code, out = self.run_main([str(self.td)])
        self.assertEqual(code, 0)
        self.assertIn("预演", out)
        self.assertIn("2 个文件待整理", out)
        self.assertTrue((self.td / "photo.jpg").exists())  # 没动

    def test_apply_and_summary(self):
        self.touch("photo.jpg")
        code, out = self.run_main([str(self.td), "--apply"])
        self.assertEqual(code, 0)
        self.assertIn("已整理 1 个文件", out)
        self.assertIn("撤销日志", out)
        self.assertTrue((self.td / "Images" / "photo.jpg").exists())

    def test_apply_then_undo(self):
        self.touch("photo.jpg")
        self.run_main([str(self.td), "--apply"])
        code, out = self.run_main([str(self.td), "--undo"])
        self.assertEqual(code, 0)
        self.assertIn("已撤销 1 个文件", out)
        self.assertTrue((self.td / "photo.jpg").exists())

    def test_consecutive_default_undos_walk_back_through_batches(self):
        for name in ("first.txt", "second.txt"):
            self.touch(name)
            self.assertEqual(self.run_main([str(self.td), "--apply"])[0], 0)
        for name in ("second.txt", "first.txt"):
            code, output = self.run_main([str(self.td), "--undo"])
            self.assertEqual(code, 0)
            self.assertIn("已撤销 1 个文件", output)
            self.assertTrue((self.td / name).is_file())
        self.assertEqual(self.run_main([str(self.td), "--undo"])[0], 2)

    def test_undo_without_log(self):
        code, out = self.run_main([str(self.td), "--undo"])
        self.assertEqual(code, 2)

    def test_explicit_log_after_relative_apply_from_different_directory(self):
        self.touch("notes.txt")
        self.addCleanup(os.chdir, Path.cwd())
        os.chdir(self.td.parent)
        self.assertEqual(self.run_main([self.td.name, "--apply"])[0], 0)
        log, = self.td.glob("tidydl-undo-*.json")
        os.chdir(self.td)
        with patch.object(Path, "home", return_value=self.td / "no-home"):
            code, out = self.run_main(["--undo", str(log)])
        self.assertEqual(code, 0)
        self.assertIn("已撤销 1 个文件", out)
        self.assertEqual((self.td / "notes.txt").read_text(encoding="utf-8"), "内容")

    def test_nothing_to_do(self):
        code, out = self.run_main([str(self.td)])
        self.assertEqual(code, 0)
        self.assertIn("没有需要整理的文件", out)

    def test_bad_dir(self):
        code, out = self.run_main(["Z:/definitely/not/here"])
        self.assertEqual(code, 2)

    def test_list_rules(self):
        code, out = self.run_main(["--list-rules"])
        self.assertEqual(code, 0)
        self.assertIn("Installers", out)
        self.assertIn("安装包", out)

    def test_custom_rules_via_cli(self):
        self.touch("poster.avif")
        f = self.td / "rules.json"
        f.write_text(json.dumps({"Images": ["avif"]}), encoding="utf-8")
        code, out = self.run_main([str(self.td), "--rules", str(f), "--apply"])
        self.assertEqual(code, 0)
        self.assertTrue((self.td / "Images" / "poster.avif").exists())

    def test_min_age_zero_via_cli(self):
        p = self.touch("fresh.pdf", age_minutes=0)
        code, out = self.run_main([str(self.td), "--min-age", "0"])
        self.assertEqual(code, 0)
        self.assertIn("1 个文件待整理", out)

    def test_version(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("tidydl", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
