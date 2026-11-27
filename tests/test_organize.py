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

from tidydl.organize import apply_plan, find_latest_undo_log, scan, undo


class Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def touch(self, name: str, age_minutes: float = 100, content: str = "x") -> Path:
        p = self.td / name
        p.write_text(content, encoding="utf-8")
        old = time.time() - age_minutes * 60
        os.utime(p, (old, old))
        return p


class TestScan(Base):
    def test_plan_categorizes_without_touching(self):
        self.touch("photo.jpg")
        self.touch("song.mp3")
        self.touch("setup.exe")
        self.touch("doc.pdf")
        report = scan(self.td)
        self.assertEqual(len(report.moves), 4)
        dests = {m.dest.parent.name for m in report.moves}
        self.assertEqual(dests, {"Images", "Audio", "Installers", "Documents"})
        for m in report.moves:
            self.assertTrue(m.src.exists())  # 预演不动文件
        self.assertFalse((self.td / "Images").exists())  # 也不建目录

    def test_skips(self):
        self.touch(".hidden.txt")
        self.touch("desktop.ini")
        self.touch("movie.crdownload")
        self.touch("fresh.pdf", age_minutes=1)  # 默认 min_age=10 跳过
        self.touch("tidydl-undo-20260101-000000.json")
        self.touch("old.jpg")
        report = scan(self.td)
        self.assertEqual(len(report.moves), 1)
        reasons = {r for _p, r in report.skipped}
        self.assertIn("隐藏/系统文件", reasons)
        self.assertIn("下载中", reasons)
        self.assertIn("最近修改", reasons)
        self.assertIn("撤销日志", reasons)

    def test_min_age_zero(self):
        self.touch("fresh.pdf", age_minutes=0)
        report = scan(self.td, min_age_minutes=0)
        self.assertEqual(len(report.moves), 1)

    def test_collision_suffix(self):
        (self.td / "Images").mkdir()
        (self.td / "Images" / "photo.jpg").write_text("旧", encoding="utf-8")
        self.touch("photo.jpg")
        report = scan(self.td)
        self.assertEqual(len(report.moves), 1)
        self.assertEqual(report.moves[0].dest.name, "photo (2).jpg")

    def test_extensionless_goes_to_others(self):
        self.touch("makefile")
        report = scan(self.td)
        self.assertEqual(report.moves[0].dest.parent.name, "Others")


class TestApplyUndo(Base):
    def _plan_with(self, names):
        for n in names:
            self.touch(n)
        return scan(self.td)

    def test_apply_moves_and_writes_log(self):
        report = self._plan_with(["a.jpg", "b.zip", "c.exe"])
        log = apply_plan(report)
        self.assertTrue(log.exists())
        self.assertTrue(log.name.startswith("tidydl-undo-"))
        self.assertTrue((self.td / "Images" / "a.jpg").exists())
        self.assertTrue((self.td / "Archives" / "b.zip").exists())
        self.assertFalse((self.td / "a.jpg").exists())

    def test_undo_restores_everything(self):
        report = self._plan_with(["a.jpg", "b.zip"])
        log = apply_plan(report)
        n = undo(log)
        self.assertEqual(n, 2)
        self.assertTrue((self.td / "a.jpg").exists())
        self.assertTrue((self.td / "b.zip").exists())

    def test_undo_keeps_both_when_name_taken(self):
        report = self._plan_with(["a.jpg"])
        log = apply_plan(report)
        (self.td / "a.jpg").write_text("新下载的同名文件", encoding="utf-8")
        n = undo(log)
        self.assertEqual(n, 1)
        self.assertTrue((self.td / "a.jpg").exists())       # 占名的新文件
        self.assertTrue((self.td / "a (2).jpg").exists())   # 恢复回来的旧文件

    def test_idempotent_rerun(self):
        report = self._plan_with(["a.jpg", "b.zip"])
        apply_plan(report)
        again = scan(self.td)
        self.assertEqual(len(again.moves), 0)  # 已在分类目录里,顶层无文件

    def test_find_latest_undo_log(self):
        self.assertIsNone(find_latest_undo_log(self.td))
        report = self._plan_with(["a.jpg"])
        apply_plan(report)
        self.assertIsNotNone(find_latest_undo_log(self.td))

    def test_same_second_batches_keep_independent_logs(self):
        with patch("tidydl.organize.time.strftime", return_value="20261127-120000"):
            first = apply_plan(self._plan_with(["first.txt"]))
            old_contents = first.read_bytes()
            second = apply_plan(self._plan_with(["second.txt"]))
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), old_contents)
        self.assertEqual(find_latest_undo_log(self.td), second)
        self.assertEqual(undo(second), 1)
        self.assertTrue((self.td / "Documents" / "first.txt").is_file())
        self.assertEqual(undo(first), 1)

    def test_partial_batch_can_be_undone(self):
        report = self._plan_with(["a.jpg", "b.jpg"])
        (self.td / "b.jpg").unlink()
        with self.assertRaises(FileNotFoundError):
            apply_plan(report)
        log = find_latest_undo_log(self.td)
        self.assertIsNotNone(log)
        self.assertEqual(undo(log), 1)
        self.assertTrue((self.td / "a.jpg").is_file())

    def test_legacy_relative_log_from_another_working_directory(self):
        images = self.td / "Images"
        images.mkdir()
        (images / "a.jpg").write_bytes(b"photo")
        log = self.td / "tidydl-undo-20260101-120000.json"
        log.write_text(json.dumps({"version": 1, "root": "Downloads", "moves": [
            ["Downloads/a.jpg", "Downloads/Images/a.jpg"]
        ]}), encoding="utf-8")
        self.assertEqual(undo(log), 1)
        self.assertEqual((self.td / "a.jpg").read_bytes(), b"photo")


if __name__ == "__main__":
    unittest.main()
