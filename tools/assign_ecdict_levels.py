# -*- coding: utf-8 -*-
"""
仅更新 unit 为「ECDICT 预置」的词条的 lesson 字段，按学段分级；其余词条不改。

分级依据（与 ECDICT 的 tag / 词频列一致，见 skywind3000/ECDICT）：
  - tag 含 cet6 → 英语六级
  - tag 含 cet4（且无 cet6）→ 英语四级
  - tag 含 zk（中考）→ 初中（与 gk 同时出现时优先初中）
  - tag 含 gk（高考）且无 zk → 高中
  - tag 含 ky / gre / toefl / ielts → 英语六级（留学/考研类归入最高中学段档）
  - 无上述标签时，用 frq（无则用 bnc）作当代/传统语料词频序号的近似：
      ≤4500 → 小学，≤15000 → 初中，其余 → 高中

用法：在项目根目录执行
  python tools/assign_ecdict_levels.py
可选：python tools/assign_ecdict_levels.py --ecdict path/to/ecdict.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORDS_PATH = ROOT / "words.json"
ECDICT_DEFAULT = ROOT / "ECDICT-master" / "ECDICT-master" / "ecdict.csv"

ECDICT_UNIT = "ECDICT 预置"

def _strip_word(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (w or "").lower())


def _parse_int(s: str) -> int:
    try:
        n = int(float((s or "").strip()))
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return 0


def classify_level(tag: str, frq: int, bnc: int) -> str:
    parts = (tag or "").lower().split()

    if "cet6" in parts or "cet6star" in parts:
        return "英语六级"
    if "cet4" in parts:
        return "英语四级"
    # 中考早于高考：同时带 zk/gk 时按较低学段（与课标常见标法一致）
    if "zk" in parts:
        return "初中"
    if "gk" in parts:
        return "高中"
    if any(k in parts for k in ("ky", "gre", "toefl", "ielts")):
        return "英语六级"

    rk = frq if frq > 0 else (bnc if bnc > 0 else 10**9)
    if rk <= 4500:
        return "小学"
    if rk <= 15000:
        return "初中"
    return "高中"


def _rank_tuple(tag: str, frq: int, bnc: int) -> tuple[int, int]:
    """越小越好：优先更小词频/语料序；同频时优先带 tag 的行。"""
    f = frq if frq > 0 else (bnc if bnc > 0 else 10**9)
    empty = 0 if (tag or "").strip() else 1
    return (f, empty)


def load_ecdict_meta(ecdict_path: Path, need_sw: set[str]) -> dict[str, tuple[str, int, int]]:
    """need_sw: stripword keys。返回 sw -> (tag, frq, bnc)。同形多行取词频更小、尽量有 tag 的主条。"""
    out: dict[str, tuple[str, int, int]] = {}
    if not ecdict_path.is_file():
        raise FileNotFoundError(ecdict_path)

    with open(ecdict_path, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        fn = {n.strip().lower(): n.strip() for n in (reader.fieldnames or []) if n and n.strip()}

        def col(row: dict[str, str], key: str) -> str:
            k = fn.get(key.lower())
            return (row.get(k or "", "") or "").strip() if k else ""

        for row in reader:
            w = col(row, "word")
            sw = _strip_word(w)
            if sw not in need_sw:
                continue
            tag = col(row, "tag")
            frq = _parse_int(col(row, "frq"))
            bnc = _parse_int(col(row, "bnc"))
            cand = (tag, frq, bnc)
            if sw not in out or _rank_tuple(tag, frq, bnc) < _rank_tuple(*out[sw]):
                out[sw] = cand
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--words-json", type=Path, default=WORDS_PATH)
    ap.add_argument("--ecdict", type=Path, default=ECDICT_DEFAULT)
    args = ap.parse_args()

    data = json.loads(args.words_json.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        print("words.json 格式错误：应为数组")
        return 1

    need: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        if str(item.get("unit", "")).strip() != ECDICT_UNIT:
            continue
        en = str(item.get("en", "")).strip()
        sw = _strip_word(en)
        if sw:
            need.add(sw)

    if not need:
        print(f"没有 unit 为「{ECDICT_UNIT}」的词条，无需处理。")
        return 0

    try:
        meta = load_ecdict_meta(args.ecdict, need)
    except FileNotFoundError as e:
        print("找不到 ecdict.csv：", e, "\n请用 --ecdict 指定解压后的 ecdict.csv 路径。")
        return 1

    counts: Counter[str] = Counter()
    missing = 0
    changed = 0

    for item in data:
        if not isinstance(item, dict):
            continue
        if str(item.get("unit", "")).strip() != ECDICT_UNIT:
            continue
        en = str(item.get("en", "")).strip()
        sw = _strip_word(en)
        tag, frq, bnc = meta.get(sw, ("", 0, 0))
        if sw not in meta:
            missing += 1
        level = classify_level(tag, frq, bnc)
        counts[level] += 1
        old = str(item.get("lesson", "")).strip()
        if old != level:
            changed += 1
        item["lesson"] = level

    args.words_json.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"已更新「{ECDICT_UNIT}」共 {sum(counts.values())} 条（其中改写 lesson {changed} 条）。")
    print("分级统计：", dict(sorted(counts.items(), key=lambda x: (-x[1], x[0]))))
    if missing:
        print(f"提示：有 {missing} 条在 ecdict.csv 中未匹配到（已按无标签词频规则分级）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
