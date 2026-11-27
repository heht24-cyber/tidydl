"""命令行入口:预演默认,--apply 执行,--undo 撤销。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .organize import apply_plan, find_latest_undo_log, fmt_bytes, scan, undo
from .rules import CATEGORIES, CN_NAMES, load_rules


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tidydl",
        description="下载文件夹一键收拾:零配置分类整理,默认预演,可撤销。",
        epilog="示例:\n"
               "  tidydl                     # 预演整理默认下载文件夹\n"
               "  tidydl D:\\Downloads --apply\n"
               "  tidydl --undo              # 撤销最近一次整理\n"
               "  tidydl --rules my.json --list-rules",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("path", nargs="?", help="要整理的目录(默认:~/Downloads)")
    p.add_argument("--apply", action="store_true", help="真正执行(默认只预演)")
    p.add_argument("--undo", nargs="?", const="", default=None, metavar="LOG",
                   help="撤销:不带参数=撤销目标目录最近一次;也可指定日志文件")
    p.add_argument("--rules", metavar="FILE", help="自定义规则 JSON(与内置合并)")
    p.add_argument("--min-age", type=float, default=10, metavar="MIN",
                   help="最近 MIN 分钟内修改的文件跳过(默认 10;0=不跳过)")
    p.add_argument("--list-rules", action="store_true", help="打印内置规则后退出")
    p.add_argument("--version", action="version", version=f"tidydl {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)

    if args.list_rules:
        for name, exts in CATEGORIES.items():
            label = f"{CN_NAMES.get(name, name)} {name}"
            print(f"  {label:<16} {' '.join(sorted(exts)) if exts else '(兜底)'}")
        return 0

    root = Path(args.path).expanduser() if args.path else Path.home() / "Downloads"
    if not root.is_dir():
        sys.stderr.write(f"目录不存在:{root}\n")
        return 2

    if args.undo is not None:
        log = Path(args.undo).expanduser() if args.undo else find_latest_undo_log(root)
        if log is None or not Path(log).is_file():
            sys.stderr.write(f"找不到撤销日志:{log}\n")
            return 2
        n = undo(Path(log))
        print(f"已撤销 {n} 个文件(依据 {Path(log).name})")
        return 0

    rules = None
    if args.rules:
        try:
            rules = load_rules(Path(args.rules).expanduser())
        except (ValueError, OSError) as exc:
            sys.stderr.write(f"规则文件加载失败:{exc}\n")
            return 2

    report = scan(root, rules=rules, min_age_minutes=args.min_age)
    print(f"tidydl · {root}")
    if not report.moves:
        print("  没有需要整理的文件 ✅")
        return 0

    print(f"  {len(report.moves)} 个文件待整理:")
    for cat, moves in sorted(report.by_category().items()):
        size = sum(m.size for m in moves)
        print(f"    {CN_NAMES.get(cat, cat)} {cat:<12} {len(moves):>4} 个  {fmt_bytes(size):>10}")
    for m in report.moves[:30]:
        print(f"      {m.src.name} → {m.dest.parent.name}{os.sep}{m.dest.name}")
    if len(report.moves) > 30:
        print(f"      …(其余 {len(report.moves) - 30} 个略)")
    if report.skipped:
        reasons: dict[str, int] = {}
        for _p, r in report.skipped:
            reasons[r] = reasons.get(r, 0) + 1
        detail = " / ".join(f"{r} {n}" for r, n in sorted(reasons.items()))
        print(f"  跳过 {len(report.skipped)} 个({detail})")

    if not args.apply:
        print("\n  预演模式:确认无误后加 --apply 执行;执行后 --undo 可一键撤销。")
        return 0

    log = apply_plan(report)
    print(f"\n  已整理 {len(report.moves)} 个文件;撤销日志:{log.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
