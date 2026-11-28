"""扫描、计划、执行与撤销。核心不变量:预演不动任何文件;执行只移动、不删除。"""

from __future__ import annotations

import json
import sqlite3
import stat
import shutil
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path

from .rules import IN_PROGRESS_EXTS, SYSTEM_JUNK, UNDO_PREFIX, category_for

UNDO_LOG_VERSION = 1


@dataclass
class Move:
    src: Path
    dest: Path
    size: int


@dataclass
class Report:
    root: Path
    moves: list[Move] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)

    def by_category(self) -> dict[str, list[Move]]:
        out: dict[str, list[Move]] = {}
        for m in self.moves:
            out.setdefault(m.dest.parent.name, []).append(m)
        return out


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 2
    while True:
        cand = dest.with_name(f"{stem} ({n}){suffix}")
        if not cand.exists():
            return cand
        n += 1


def scan(root: Path, rules: dict[str, set[str]] | None = None,
         min_age_minutes: float = 10, now: float | None = None) -> Report:
    """只看顶层文件,生成移动计划。不创建目录、不动文件。"""
    now = time.time() if now is None else now
    report = Report(root=root)
    for item in sorted(root.iterdir()):
        if not item.is_file():
            continue
        name = item.name
        low = name.lower()
        if name.startswith(".") or low in SYSTEM_JUNK:
            report.skipped.append((item, "隐藏/系统文件"))
            continue
        if name.startswith(UNDO_PREFIX):
            report.skipped.append((item, "撤销日志"))
            continue
        if item.suffix.lower() in IN_PROGRESS_EXTS:
            report.skipped.append((item, "下载中"))
            continue
        try:
            mtime = item.stat().st_mtime
        except OSError:
            report.skipped.append((item, "无法读取"))
            continue
        if (now - mtime) < min_age_minutes * 60:
            report.skipped.append((item, "最近修改"))
            continue
        category = category_for(item.suffix, rules)
        dest_dir = root / category
        dest = _unique_dest(dest_dir / name)
        try:
            size = item.stat().st_size
        except OSError:
            size = 0
        report.moves.append(Move(src=item, dest=dest, size=size))
    return report


def _reserve_undo_log(root: Path) -> tuple[Path, Path]:
    """用临时文件独占本批次的名称。完成的日志才使用 .json 后缀。"""
    ts = time.strftime("%Y%m%d-%H%M%S")
    for batch in count():
        suffix = f"_{batch:06d}" if batch else ""
        log = root / f"{UNDO_PREFIX}{ts}{suffix}.json"
        pending = log.with_suffix(".pending")
        try:
            pending.touch(exist_ok=False)
        except FileExistsError:
            continue
        if not log.exists():
            return log, pending
        pending.unlink()


def _write_json(path: Path, data: dict) -> None:
    # The directory lock gives this temporary file a single writer.
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _read_log(log: Path) -> dict:
    data = json.loads(log.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != UNDO_LOG_VERSION:
        raise ValueError(f"不支持的撤销日志: {log.name}")
    moves = data.get("moves")
    if not isinstance(moves, list) or any(
        not isinstance(pair, list) or len(pair) != 2 or
        not all(isinstance(value, str) for value in pair) for pair in moves
    ):
        raise ValueError(f"撤销日志中的移动记录无效: {log.name}")
    return data


def _original_path(log: Path, data: dict, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    # Older logs stored paths prefixed by the relative scan directory.
    return log.parent / path.relative_to(data["root"])


def _recover_undo(cursor: Path) -> None:
    if not cursor.exists():
        return
    intent = json.loads(cursor.read_text(encoding="utf-8"))
    log = cursor.parent / intent["log"]
    data = _read_log(log)
    left = intent["left"]
    if len(data["moves"]) >= left:
        dest = _original_path(log, data, data["moves"][left - 1][1])
        # A completed rename removes dest. If an unrelated file has since
        # occupied dest, comparing its identity also prevents replay.
        try:
            info = dest.lstat()
            same = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns] == intent["file"]
        except FileNotFoundError:
            same = False
        if not same:
            left -= 1
        data["moves"] = data["moves"][:left]
        _write_json(log, data)
    cursor.unlink()


@contextmanager
def _directory_lock(directory: Path):
    # SQLite supplies a process lock on Windows, macOS and Linux. The OS
    # releases it when a process exits, even if its undo was interrupted.
    with closing(sqlite3.connect(directory / ".tidydl.lock", timeout=30)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        cursor = directory / ".tidydl-undo-progress.json"
        _recover_undo(cursor)
        yield cursor


def apply_plan(report: Report) -> Path:
    with _directory_lock(report.root):
        return _apply_plan(report)


def _apply_plan(report: Report) -> Path:
    """执行计划并记录成功的移动。中途遇到错误时,已移动的文件仍可撤销。"""
    log_path, pending = _reserve_undo_log(report.root)
    pairs: list[list[str]] = []
    try:
        for m in report.moves:
            m.dest.parent.mkdir(parents=True, exist_ok=True)
            m.dest = _unique_dest(m.dest)
            try:
                m.src.rename(m.dest)
            except OSError:
                shutil.move(str(m.src), str(m.dest))
            pairs.append([str(m.src.absolute()), str(m.dest.absolute())])
    finally:
        if pairs or not report.moves:
            pending.write_text(
                json.dumps({"version": UNDO_LOG_VERSION, "root": str(report.root.absolute()),
                            "moves": pairs}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            pending.replace(log_path)
        else:
            pending.unlink()
    return log_path


def find_latest_undo_log(root: Path) -> Path | None:
    for log in sorted(root.glob(UNDO_PREFIX + "*.json"), reverse=True):
        try:
            data = _read_log(log)
        except ValueError:
            continue
        if any(_original_path(log, data, dest).is_file() for _, dest in data["moves"]):
            return log
    return None


def undo_latest(root: Path) -> tuple[Path, int] | None:
    with _directory_lock(root) as cursor:
        log = find_latest_undo_log(root)
        if log is None:
            return None
        return log, _undo(log.absolute(), cursor)


def undo(log_path: Path) -> int:
    log = Path(log_path).absolute()
    with _directory_lock(log.parent) as cursor:
        return _undo(log, cursor)


def _undo(log: Path, cursor: Path) -> int:
    data = _read_log(log)
    moves = data["moves"]
    left, done = len(moves), 0
    try:
        while left:
            src_value, dest_value = moves[left - 1]
            src = _original_path(log, data, src_value)
            dest = _original_path(log, data, dest_value)
            try:
                info = dest.lstat()
            except FileNotFoundError:
                left -= 1
                continue
            if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                src.parent.mkdir(parents=True, exist_ok=True)
                target = _unique_dest(src)
                # Store a small cursor before moving. A restart can decide
                # whether this move finished before another batch reuses dest.
                _write_json(cursor, {"log": log.name, "left": left, "file": [
                    info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]})
                shutil.move(str(dest), str(target))
                done += 1
            left -= 1
    finally:
        if left < len(moves):
            data["moves"] = moves[:left]
            _write_json(log, data)
        # If a move raised after changing the filesystem, leave the intent for
        # the next locked operation to reconcile. Earlier moves are checkpointed.
    cursor.unlink(missing_ok=True)
    return done


def fmt_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TiB"
