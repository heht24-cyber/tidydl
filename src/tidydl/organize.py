"""扫描、计划、执行与撤销。核心不变量:预演不动任何文件;执行只移动、不删除。"""

from __future__ import annotations

import json
import shutil
import time
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


def apply_plan(report: Report) -> Path:
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
    logs = sorted(root.glob(UNDO_PREFIX + "*.json"))
    return logs[-1] if logs else None


def undo(log_path: Path) -> int:
    """按日志逆序还原。原名被占用时保留两者(恢复文件加序号)。"""
    log_path = Path(log_path).absolute()
    data = json.loads(log_path.read_text(encoding="utf-8"))
    if data.get("version") != UNDO_LOG_VERSION:
        raise ValueError(f"不支持的日志版本:{data.get('version')}")
    # 旧版从相对目录整理时,路径包含当时传入的目录前缀。
    def original_path(value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return log_path.parent / path.relative_to(data["root"])

    done = 0
    for src_s, dest_s in reversed(data.get("moves", [])):
        src, dest = original_path(src_s), original_path(dest_s)
        if not dest.exists():
            continue
        src.parent.mkdir(parents=True, exist_ok=True)
        target = _unique_dest(src)
        try:
            dest.rename(target)
        except OSError:
            shutil.move(str(dest), str(target))
        done += 1
    return done


def fmt_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TiB"
