# -*- coding: utf-8 -*-
"""
从本地 ECDICT CSV 中按词频挑选词条，合并进项目根目录的 words.json。

数据说明与下载： https://github.com/skywind3000/ECDICT （MIT）

用法（先在仓库目录放好 ecdict.csv 或 ecdict.mini.csv）：
  python tools/import_ecdict_preset.py ecdict.csv --limit 3000 --max-rank 20000

默认归入单元「ECDICT 预置」、部分「高频」，与现有词库去重规则一致（同 en+pos+unit+lesson 跳过）。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dictation_core as dc
from word_import import (
    _ecdict_cell,
    _ecdict_field_lookup,
    _ecdict_first_pos,
    _ecdict_normalize_zh,
)


def _rank(row: dict[str, str], lookup: dict[str, str]) -> int:
    def gint(key: str) -> int:
        v = _ecdict_cell(row, lookup, key)
        try:
            n = int(float(v))
            return n if n > 0 else 0
        except (TypeError, ValueError):
            return 0

    frq = gint("frq")
    bnc = gint("bnc")
    if frq > 0:
        return frq
    if bnc > 0:
        return bnc
    return 10**9


def _is_simple_word(en: str) -> bool:
    en = en.strip()
    if len(en) < 2 or len(en) > 48:
        return False
    if " " in en or "\t" in en:
        return False
    return bool(re.match(r"^[a-zA-Z][a-zA-Z\-']*$", en))


def _row_to_item(row: dict[str, str], lookup: dict[str, str]) -> dict[str, str] | None:
    en = _ecdict_cell(row, lookup, "word")
    zh = _ecdict_normalize_zh(_ecdict_cell(row, lookup, "translation"))
    if not en or not zh or not re.search(r"[\u4e00-\u9fff]", zh):
        return None
    pos = _ecdict_first_pos(_ecdict_cell(row, lookup, "pos"))
    item: dict[str, str] = {"en": en, "zh": zh}
    if pos:
        item["pos"] = pos
    return item


def main() -> int:
    ap = argparse.ArgumentParser(description="从 ECDICT CSV 合并预置高频词到 words.json")
    ap.add_argument(
        "csv_path",
        type=Path,
        nargs="?",
        default=ROOT / "ecdict.csv",
        help="ECDICT 的 csv 文件路径（若不存在请从 GitHub 下载）",
    )
    ap.add_argument("--words-json", type=Path, default=ROOT / "words.json")
    ap.add_argument("--limit", type=int, default=2500, help="最多新增条数")
    ap.add_argument(
        "--max-rank",
        type=int,
        default=20000,
        help="只保留 frq 或 bnc（有值时取较小者逻辑：优先 frq）≤ 该值的词；0 表示不按词频过滤（慎用，大表很占内存）",
    )
    ap.add_argument("--unit", default="ECDICT 预置", help="写入的单元名")
    ap.add_argument("--lesson", default="高频", help="写入的部分名")
    args = ap.parse_args()

    path = args.csv_path
    if not path.is_file():
        print(
            "找不到 CSV 文件：",
            path,
            "\n请从 https://github.com/skywind3000/ECDICT 下载 ecdict.csv 或 ecdict.mini.csv 到项目目录，",
            "或将路径作为第一个参数传入。",
            sep="",
        )
        return 1

    candidates: list[tuple[int, dict[str, str]]] = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        lookup = _ecdict_field_lookup(reader.fieldnames)
        if "word" not in lookup or "translation" not in lookup:
            print("表头不是 ECDICT 格式（需要 word、translation 列）。")
            return 1

        for row in reader:
            en = _ecdict_cell(row, lookup, "word")
            if not _is_simple_word(en):
                continue
            rk = _rank(row, lookup)
            if args.max_rank > 0 and rk > args.max_rank:
                continue
            item = _row_to_item(row, lookup)
            if item:
                candidates.append((rk, item))

    candidates.sort(key=lambda x: x[0])
    picked: list[dict[str, str]] = []
    for _, item in candidates:
        item["unit"] = args.unit
        item["lesson"] = args.lesson
        picked.append(item)
        if len(picked) >= args.limit:
            break

    if not picked:
        print("没有符合条件的词条（可提高 --max-rank 或换用完整 ecdict.csv）。")
        return 1

    words_path = args.words_json.resolve()
    if words_path.is_file():
        try:
            words = json.loads(words_path.read_text(encoding="utf-8"))
            if not isinstance(words, list):
                words = []
        except Exception:
            words = []
    else:
        words = []

    new_words, added, skipped = dc.normalize_and_merge(
        words,
        picked,
        default_unit=args.unit,
        default_lesson=args.lesson,
    )
    words_path.write_text(
        json.dumps(new_words, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已写入 {words_path}：新增 {added} 条，跳过（已存在）{skipped} 条。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
