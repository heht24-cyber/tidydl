"""扫描、计划、执行与撤销。核心不变量:预演不动任何文件;执行只移动、不删除。"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
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


def apply_plan(report: Report) -> Path:
    """执行计划,返回撤销日志路径。执行前对每个目标再做一次防撞。"""
    ts = time.strftime("%Y%m%d-%H%M%S")
    log_path = report.root / f"{UNDO_PREFIX}{ts}.json"
    pairs: list[list[str]] = []
    for m in report.moves:
        m.dest.parent.mkdir(parents=True, exist_ok=True)
        m.dest = _unique_dest(m.dest)
        try:
            m.src.rename(m.dest)
        except OSError:
            shutil.move(str(m.src), str(m.dest))
        pairs.append([str(m.src), str(m.dest)])
    log_path.write_text(
        json.dumps({"version": UNDO_LOG_VERSION, "root": str(report.root),
                    "moves": pairs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return log_path


def find_latest_undo_log(root: Path) -> Path | None:
    logs = sorted(root.glob(UNDO_PREFIX + "*.json"))
    return logs[-1] if logs else None


def undo(log_path: Path) -> int:
    """按日志逆序还原。原名被占用时保留两者(恢复文件加序号)。"""
    data = json.loads(Path(log_path).read_text(encoding="utf-8"))
    if data.get("version") != UNDO_LOG_VERSION:
        raise ValueError(f"不支持的日志版本:{data.get('version')}")
    done = 0
    for src_s, dest_s in reversed(data.get("moves", [])):
        src, dest = Path(src_s), Path(dest_s)
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
