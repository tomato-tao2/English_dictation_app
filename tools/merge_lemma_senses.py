# -*- coding: utf-8 -*-
"""
将词库 JSON 中同一 en + unit + lesson 的多条合并为一条（义项写入 senses）。

用法（项目根目录）：
  python tools/merge_lemma_senses.py --dry-run
  python tools/merge_lemma_senses.py              # 写回并备份
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dictation_core as dc  # noqa: E402


def _load_list(path: Path) -> list:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _process(path: Path, dry_run: bool) -> tuple[int, int, bool]:
    raw = _load_list(path)
    if not raw:
        return 0, 0, False
    merged, _, _ = dc.normalize_and_merge(raw, [], "Unit 1", "全部")
    old_n, new_n = len(raw), len(merged)
    changed = old_n != new_n or json.dumps(raw, ensure_ascii=False) != json.dumps(
        merged, ensure_ascii=False
    )
    if dry_run or not changed:
        return old_n, new_n, changed
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_name(f"{path.stem}.backup.{ts}.json")
    shutil.copy2(path, bak)
    dc.save_words_to_path(path, merged)
    return old_n, new_n, changed


def main() -> None:
    ap = argparse.ArgumentParser(description="合并词库中同形多义项词条")
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    args = ap.parse_args()

    targets = [ROOT / "words.json"] + list((ROOT / "libraries").glob("*.json"))
    for path in targets:
        if not path.is_file():
            continue
        old_n, new_n, changed = _process(path, args.dry_run)
        msg = f"{path.relative_to(ROOT)}: {old_n} -> {new_n} 条"
        if not changed:
            msg += "（无需修改）"
        print(msg)
    if args.dry_run:
        print("--dry-run：未写盘。")


if __name__ == "__main__":
    main()
