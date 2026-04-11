# -*- coding: utf-8 -*-
"""Parse user / Doubao export lines and CSV rows into word dicts."""

from __future__ import annotations

import csv
import io
import re
from typing import Any


def normalize_word(item: dict[str, Any]) -> dict[str, str] | None:
    """Return {en, zh} or {en, pos, zh}; None if invalid."""
    if not isinstance(item, dict):
        return None
    en = str(item.get("en", "")).strip()
    zh = str(item.get("zh", "")).strip()
    pos = str(item.get("pos", "")).strip()
    if not en or not zh:
        return None
    out: dict[str, str] = {"en": en, "zh": zh}
    if pos:
        out["pos"] = pos
    return out


def parse_line(line: str) -> dict[str, str] | None:
    """
    One line formats supported (try in order):
    - English,词性,中文;义项2   (Doubao CSV-style, recommended)
    - English,词性.中文;义项2   (older dot style)
    - English,中文              (simple, no POS)
    - English\\t中文
    """
    line = line.strip().replace("，", ",")
    if not line or line.startswith("#"):
        return None

    if "\t" in line and "," not in line.split("\t", 1)[0]:
        parts = line.split("\t", 1)
        if len(parts) == 2:
            en, zh = parts[0].strip(), parts[1].strip()
            return {"en": en, "zh": zh} if en and zh else None

    if "," not in line:
        return None

    # Doubao: English,POS,Chinese;more  (three segments, comma-separated)
    if line.count(",") >= 2:
        en, pos, zh = [p.strip() for p in line.split(",", 2)]
        if en and pos and zh and _looks_like_pos(pos):
            return {"en": en, "pos": pos, "zh": zh}

    first_comma = line.index(",")
    en = line[:first_comma].strip()
    rest = line[first_comma + 1 :].strip()
    if not en or not rest:
        return None

    # Doubao: after comma, "词性.义项;义项"
    dot_idx = rest.find(".")
    if dot_idx != -1:
        pos_candidate = rest[:dot_idx].strip()
        zh_part = rest[dot_idx + 1 :].strip()
        # Heuristic: pos is short (e.g. n. v. adj.); zh has Chinese or digits
        if pos_candidate and zh_part and _looks_like_pos(pos_candidate):
            return {"en": en, "pos": pos_candidate, "zh": zh_part}

    # Plain en,zh
    return {"en": en, "zh": rest}


def _looks_like_pos(s: str) -> bool:
    """Avoid treating long English glosses as '词性'."""
    s = s.strip()
    if not s or len(s) > 10:
        return False
    if re.match(
        r"^(n|v|adj|adv|vt|vi|prep|conj|pron|art|num|int|abbr)\.?$",
        s,
        re.I,
    ):
        return True
    # bare n / v / adj as in "space,n,太空"
    if re.match(r"^(n|v)$", s, re.I):
        return True
    if re.match(r"^[\u4e00-\u9fff]{1,4}\.?$", s):
        return True
    if re.match(r"^[a-z]{1,5}\.$", s, re.I):
        return True
    return False


def parse_batch_text(text: str) -> tuple[list[dict[str, str]], list[str]]:
    """
    Return (ok_words, error_lines).
    Supports section headers:
    - unit1 lesson1
    - unit 1 lesson 1
    - lesson2  (reuse current unit)
    """
    ok: list[dict[str, str]] = []
    errors: list[str] = []
    current_unit = ""
    current_lesson = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        unit, lesson = _parse_section_header(line)
        if unit or lesson:
            if unit:
                current_unit = unit
            if lesson:
                current_lesson = lesson
            continue

        w = parse_line(line)
        if w:
            if current_unit:
                w["unit"] = current_unit
            if current_lesson:
                w["lesson"] = current_lesson
            ok.append(w)
        else:
            errors.append(line[:80])
    return ok, errors


def _ecdict_normalize_zh(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = re.sub(r"\r\n|\r|\n", ";", s)
    s = re.sub(r";{2,}", ";", s)
    return s.strip("; \t")


def _ecdict_first_pos(pos: str) -> str:
    """ECDICT pos 形如 n:46/v:54 或 n. 名词…，取首个简明词性。"""
    pos = (pos or "").strip()
    if not pos:
        return ""
    chunk = pos.split("/")[0].strip()
    m = re.match(r"^([a-z]+)(?:\s*[.:：]|$)", chunk, re.I)
    if m:
        return m.group(1).lower()[:6]
    return ""


def _ecdict_field_lookup(fieldnames: list[str] | None) -> dict[str, str]:
    """列名大小写不敏感 → 原始表头字符串。"""
    if not fieldnames:
        return {}
    return {str(n).strip().lower(): str(n).strip() for n in fieldnames if str(n).strip()}


def _ecdict_cell(row: dict[str, str], lookup: dict[str, str], key: str) -> str:
    col = lookup.get(key.lower())
    if not col:
        return ""
    return str(row.get(col, "") or "").strip()


def parse_ecdict_csv_text(
    csv_text: str,
    *,
    max_rows: int | None = 8000,
) -> tuple[list[dict[str, str]], list[str]]:
    """
    解析 [ECDICT](https://github.com/skywind3000/ECDICT) 风格 CSV（表头含 word、translation）。
    中文义项里的换行合并为分号；词性取 pos 字段首段简写。
    max_rows：网页一次导入上限；命令行工具可传 None 不限制。
    """
    ok: list[dict[str, str]] = []
    errors: list[str] = []
    f = io.StringIO(csv_text.strip())
    try:
        reader = csv.DictReader(f)
    except Exception as e:
        return [], [str(e)]

    lookup = _ecdict_field_lookup(reader.fieldnames)
    if "word" not in lookup or "translation" not in lookup:
        return [], ["不是 ECDICT 格式：表头需包含 word 与 translation 列。"]

    for row in reader:
        if max_rows is not None and len(ok) >= max_rows:
            break
        en = _ecdict_cell(row, lookup, "word")
        zh = _ecdict_normalize_zh(_ecdict_cell(row, lookup, "translation"))
        if not en or not zh:
            continue
        if not re.search(r"[\u4e00-\u9fff]", zh):
            errors.append(en[:40])
            continue
        pos_raw = _ecdict_cell(row, lookup, "pos")
        pos = _ecdict_first_pos(pos_raw)
        item: dict[str, str] = {"en": en, "zh": zh}
        if pos:
            item["pos"] = pos
        ok.append(item)

    return ok, errors


def _csv_first_line_looks_ecdict(first_line: str) -> bool:
    s = first_line.strip().lower()
    return s.startswith("word,") and ",translation," in s


def _parse_section_header(line: str) -> tuple[str, str]:
    s = line.strip().lower().replace("，", ",")
    s = re.sub(r"\s+", " ", s)

    m = re.match(r"^unit\s*([a-z0-9]+)\s*lesson\s*([a-z0-9]+)$", s, re.I)
    if m:
        return f"Unit {m.group(1)}", f"Lesson {m.group(2)}"

    m = re.match(r"^unit([a-z0-9]+)\s*lesson([a-z0-9]+)$", s, re.I)
    if m:
        return f"Unit {m.group(1)}", f"Lesson {m.group(2)}"

    m = re.match(r"^lesson\s*([a-z0-9]+)$", s, re.I)
    if m:
        return "", f"Lesson {m.group(1)}"

    m = re.match(r"^lesson([a-z0-9]+)$", s, re.I)
    if m:
        return "", f"Lesson {m.group(1)}"

    return "", ""


def _header_map(header: list[str]) -> dict[str, int]:
    """Map canonical keys to column index."""
    aliases = {
        "en": ("en", "english", "word", "单词", "英文", "eng"),
        "zh": ("zh", "chinese", "meaning", "中文", "释义", "意思", "翻译"),
        "pos": ("pos", "part", "speech", "词性", "词类"),
    }
    lower = [h.strip().lower() for h in header]
    idx: dict[str, int] = {}
    for key, names in aliases.items():
        for i, cell in enumerate(lower):
            if cell in names:
                idx[key] = i
                break
    return idx


def parse_csv_text(csv_text: str) -> tuple[list[dict[str, str]], list[str]]:
    """Parse CSV string (UTF-8). Supports 2 cols (en,zh) or 3 (en,pos,zh)；ECDICT 全表见 parse_ecdict_csv_text。"""
    raw = csv_text.strip().lstrip("\ufeff")
    if not raw:
        return [], []
    first = raw.splitlines()[0]
    if _csv_first_line_looks_ecdict(first):
        return parse_ecdict_csv_text(csv_text, max_rows=8000)

    ok: list[dict[str, str]] = []
    errors: list[str] = []
    f = io.StringIO(raw)
    try:
        reader = csv.reader(f)
        rows = list(reader)
    except Exception as e:
        return [], [str(e)]

    if not rows:
        return [], []

    header = rows[0]
    hmap = _header_map(header)
    use_header = "en" in hmap and "zh" in hmap
    data_rows = rows[1:] if use_header else rows

    for row in data_rows:
        if not row or all(not (c or "").strip() for c in row):
            continue
        if use_header:
            ei, zi = hmap["en"], hmap["zh"]
            en = row[ei].strip() if ei < len(row) else ""
            zh = row[zi].strip() if zi < len(row) else ""
            pos = ""
            if "pos" in hmap:
                pi = hmap["pos"]
                pos = row[pi].strip() if pi < len(row) else ""
            if en and zh:
                w: dict[str, str] = {"en": en, "zh": zh}
                if pos:
                    w["pos"] = pos
                ok.append(w)
            elif en or zh:
                errors.append(",".join(row)[:80])
        elif len(row) >= 3:
            en, pos, zh = row[0].strip(), row[1].strip(), row[2].strip()
            if en and zh:
                w = {"en": en, "zh": zh}
                if pos:
                    w["pos"] = pos
                ok.append(w)
            else:
                errors.append(",".join(row)[:80])
        elif len(row) >= 2:
            en, zh = row[0].strip(), row[1].strip()
            if en and zh:
                ok.append({"en": en, "zh": zh})
            else:
                errors.append(",".join(row)[:80])
        else:
            errors.append(",".join(row)[:80])

    return ok, errors
