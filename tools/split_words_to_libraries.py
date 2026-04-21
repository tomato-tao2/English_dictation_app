# -*- coding: utf-8 -*-
"""
将 words.json 中 lesson 为「小学」「初中」「高中」「英语四级」「英语六级」的词条
移动到 libraries 下对应 JSON，其余保留在 words.json。

用法（在项目根目录）：
  python tools/split_words_to_libraries.py --dry-run   # 只统计不写盘
  python tools/split_words_to_libraries.py             # 写盘并备份 words.json
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


def _load_array(path: Path) -> list:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _merge_dedupe(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], int, int]:
    """按 dedupe_key 合并；返回 (新列表, 新增条数, 跳过重复条数)。"""
    seen = {dc.dedupe_key(w) for w in existing if isinstance(w, dict)}
    out = [w for w in existing if isinstance(w, dict)]
    added = 0
    skipped = 0
    for w in incoming:
        if not isinstance(w, dict):
            continue
        k = dc.dedupe_key(w)
        if k in seen:
            skipped += 1
            continue
        seen.add(k)
        out.append(w)
        added += 1
    return out, added, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description="按 lesson 拆分 words.json 到 libraries/*.json")
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    ap.add_argument("--no-backup", action="store_true", help="不写 words.json 备份")
    args = ap.parse_args()

    words_path = ROOT / "words.json"
    primary_path = ROOT / "libraries" / "primary.json"
    junior_path = ROOT / "libraries" / "junior.json"
    senior_path = ROOT / "libraries" / "senior.json"
    cet4_path = ROOT / "libraries" / "cet4.json"
    cet6_path = ROOT / "libraries" / "cet6.json"

    raw = _load_array(words_path)
    moved_p: list[dict] = []
    moved_j: list[dict] = []
    moved_s: list[dict] = []
    moved_c: list[dict] = []
    moved_c6: list[dict] = []
    rest: list = []

    for item in raw:
        if not isinstance(item, dict):
            rest.append(item)
            continue
        lesson = str(item.get("lesson", "")).strip()
        if lesson == "小学":
            moved_p.append(item)
        elif lesson == "初中":
            moved_j.append(item)
        elif lesson == "高中":
            moved_s.append(item)
        elif lesson == "英语四级":
            moved_c.append(item)
        elif lesson == "英语六级":
            moved_c6.append(item)
        else:
            rest.append(item)

    ep = _load_array(primary_path)
    ej = _load_array(junior_path)
    es = _load_array(senior_path)
    ec = _load_array(cet4_path)
    ec6 = _load_array(cet6_path)
    ep = [x for x in ep if isinstance(x, dict)]
    ej = [x for x in ej if isinstance(x, dict)]
    es = [x for x in es if isinstance(x, dict)]
    ec = [x for x in ec if isinstance(x, dict)]
    ec6 = [x for x in ec6 if isinstance(x, dict)]

    np, ap, sp = _merge_dedupe(ep, moved_p)
    nj, aj, sj = _merge_dedupe(ej, moved_j)
    ns, as_, ss = _merge_dedupe(es, moved_s)
    nc, ac, sc = _merge_dedupe(ec, moved_c)
    nc6, ac6, sc6 = _merge_dedupe(ec6, moved_c6)

    print(
        "words.json: 总条数",
        len(raw),
        "| 移出 小学",
        len(moved_p),
        "| 移出 初中",
        len(moved_j),
        "高中",
        len(moved_s),
        "英语四级",
        len(moved_c),
        "英语六级",
        len(moved_c6),
        "| 保留",
        len(rest),
    )
    print(
        "primary.json: 原有",
        len(ep),
        "合并后",
        len(np),
        "(新增",
        ap,
        "跳过重复",
        sp,
        ")",
    )
    print(
        "junior.json: 原有",
        len(ej),
        "合并后",
        len(nj),
        "(新增",
        aj,
        "跳过重复",
        sj,
        ")",
    )
    print(
        "senior.json: 原有",
        len(es),
        "合并后",
        len(ns),
        "(新增",
        as_,
        "跳过重复",
        ss,
        ")",
    )
    print(
        "cet4.json: 原有",
        len(ec),
        "合并后",
        len(nc),
        "(新增",
        ac,
        "跳过重复",
        sc,
        ")",
    )
    print(
        "cet6.json: 原有",
        len(ec6),
        "合并后",
        len(nc6),
        "(新增",
        ac6,
        "跳过重复",
        sc6,
        ")",
    )

    if args.dry_run:
        print("--dry-run：未写盘。")
        return

    if not args.no_backup and words_path.is_file():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = words_path.with_name(f"words.backup.{ts}.json")
        shutil.copy2(words_path, bak)
        print("已备份:", bak)

    dc.save_words_to_path(words_path, rest)
    dc.save_words_to_path(primary_path, np)
    dc.save_words_to_path(junior_path, nj)
    dc.save_words_to_path(senior_path, ns)
    dc.save_words_to_path(cet4_path, nc)
    dc.save_words_to_path(cet6_path, nc6)
    print("已写入 words.json 与 libraries/*.json。")


if __name__ == "__main__":
    main()
