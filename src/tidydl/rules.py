"""内置扩展名分类规则 + 自定义规则加载。"""

from __future__ import annotations

import json
from pathlib import Path

CN_NAMES = {
    "Images": "图片",
    "Videos": "视频",
    "Audio": "音频",
    "Documents": "文档",
    "Archives": "压缩包",
    "Installers": "安装包",
    "Code": "代码",
    "Fonts": "字体",
    "Others": "其他",
}

CATEGORIES: dict[str, set[str]] = {
    "Images": {
        "jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic", "heif",
        "tif", "tiff", "ico", "psd", "ai", "cr2", "nef", "arw", "dng",
    },
    "Videos": {
        "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v",
        "mpg", "mpeg", "ts", "3gp", "rm", "rmvb", "vob",
    },
    "Audio": {
        "mp3", "flac", "wav", "aac", "ogg", "m4a", "wma", "ape",
        "opus", "mid", "midi", "aiff",
    },
    "Documents": {
        "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "csv", "txt",
        "md", "rtf", "odt", "ods", "odp", "epub", "mobi", "azw3", "tex", "log",
    },
    "Archives": {
        "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst", "iso", "cab",
        "whl", "jar", "war",
    },
    "Installers": {
        "exe", "msi", "msix", "appx", "appxbundle", "dmg", "pkg",
        "deb", "rpm", "apk", "appimage",
    },
    "Code": {
        "py", "js", "ts", "jsx", "tsx", "json", "yaml", "yml", "xml",
        "html", "htm", "css", "scss", "c", "cpp", "h", "hpp", "java", "go",
        "rs", "rb", "php", "sh", "bat", "ps1", "ipynb", "sql", "ini", "toml",
    },
    "Fonts": {"ttf", "otf", "woff", "woff2"},
    "Others": set(),
}

# 正在下载的临时后缀:绝不移动
IN_PROGRESS_EXTS = {
    ".crdownload", ".part", ".partial", ".opdownload", ".tmp", ".download", ".!qb",
}

# 系统杂项:跳过
SYSTEM_JUNK = {"desktop.ini", "thumbs.db", ".ds_store"}

# 撤销日志前缀:跳过且不被整理
UNDO_PREFIX = "tidydl-undo-"


def category_for(ext: str, rules: dict[str, set[str]] | None = None) -> str:
    ext = (ext or "").lstrip(".").lower()
    table = rules if rules is not None else CATEGORIES
    for name, exts in table.items():
        if ext and ext in exts:
            return name
    return "Others"


def _validate_category_name(name: str) -> None:
    if not name or name in (".", ".."):
        raise ValueError(f"非法分类名:{name!r}")
    if "/" in name or "\\" in name or name[0] in "\\":
        raise ValueError(f"分类名不能包含路径分隔符:{name!r}")


def load_rules(path: Path) -> dict[str, set[str]]:
    """读取自定义规则 JSON,与内置规则合并后返回新表。

    格式:{"Images": ["avif"], "设计稿": ["psd", "sketch"]}
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("规则文件必须是 JSON 对象:{\"分类\": [\"扩展名\", ...]}")
    merged = {k: set(v) for k, v in CATEGORIES.items()}
    for name, exts in data.items():
        _validate_category_name(str(name))
        if not isinstance(exts, list) or not all(isinstance(e, str) for e in exts):
            raise ValueError(f"分类 {name!r} 的规则必须是字符串数组")
        new_exts = {e.lstrip(".").lower() for e in exts}
        # 覆盖语义:自定义优先,先从所有内置分类移除再加入
        for cat in merged.values():
            cat.difference_update(new_exts)
        merged.setdefault(str(name), set()).update(new_exts)
    return merged
