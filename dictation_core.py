# -*- coding: utf-8 -*-
"""Shared dictation logic (no GUI). Used by main.py (Tk) and web_app.py (Flask)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import edge_tts

SCRIPT_DIR = Path(__file__).resolve().parent
WORDS_FILE = SCRIPT_DIR / "words.json"
PROGRESS_FILE = SCRIPT_DIR / "progress.json"
WRONG_SPELL_BOOK_FILE = SCRIPT_DIR / "wrong_spell_book.json"
TTS_CACHE_DIR = SCRIPT_DIR / "tts_cache"

EDGE_VOICE_EN = "en-US-AriaNeural"
EDGE_VOICE_ZH = "zh-CN-XiaoxiaoNeural"


def edge_voice_for(language: str) -> str:
    return EDGE_VOICE_ZH if language == "zh" else EDGE_VOICE_EN


def edge_cache_path(text: str, voice: str) -> Path:
    key = hashlib.sha256(f"{voice}\0{text}".encode("utf-8")).hexdigest()
    return TTS_CACHE_DIR / f"{key}.mp3"


def edge_synthesize_to_file(text: str, voice: str, path: Path) -> None:
    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(path))

    asyncio.run(_run())


def ensure_tts_mp3(text: str, language: str) -> Path:
    """Return path to cached mp3; synthesize if missing."""
    TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    voice = edge_voice_for(language)
    path = edge_cache_path(text, voice)
    if not path.is_file():
        edge_synthesize_to_file(text, voice, path)
    return path


def load_words_from_disk() -> list[dict]:
    if not WORDS_FILE.is_file():
        return []
    with open(WORDS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [w for w in data if isinstance(w, dict) and str(w.get("en", "")).strip()]


def norm_unit(value: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    m = re.match(r"^unit\s*([a-z0-9]+)$", s, re.I)
    if m:
        return f"Unit {m.group(1)}"
    return s


def norm_lesson(value: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    m = re.match(r"^lesson\s*([a-z0-9]+)$", s, re.I)
    if m:
        return f"Lesson {m.group(1)}"
    return s


def scope_filtered_words(words: list[dict], unit: str, lesson: str) -> list[dict]:
    result: list[dict] = []
    for w in words:
        if not isinstance(w, dict):
            continue
        w_unit = norm_unit(w.get("unit", ""))
        w_lesson = norm_lesson(w.get("lesson", ""))
        unit_ok = unit == "全部单元" or unit == w_unit
        lesson_ok = lesson == "全部部分" or lesson == w_lesson
        if unit_ok and lesson_ok:
            result.append(w)
    return result


def list_units(words: list[dict]) -> list[str]:
    u = sorted({norm_unit(w.get("unit", "")) for w in words if norm_unit(w.get("unit", ""))})
    return u if u else ["Unit 1"]


def list_lessons(words: list[dict], unit: str) -> list[str]:
    lessons: set[str] = set()
    for w in words:
        if not isinstance(w, dict):
            continue
        w_unit = norm_unit(w.get("unit", ""))
        w_lesson = norm_lesson(w.get("lesson", ""))
        if not w_lesson:
            continue
        if unit == "全部单元" or unit == w_unit:
            lessons.add(w_lesson)
    return sorted(lessons)


def chinese_speech_text(word: dict) -> str:
    zh = str(word.get("zh", "")).strip()
    if not zh:
        return ""
    return zh.replace(";", "，")


def prompt_text_and_language(word: dict, dictation_mode: str) -> tuple[str, str]:
    if dictation_mode == "zh_to_en":
        return chinese_speech_text(word), "zh"
    # en_to_zh、en_spell：均播报英文单词
    return str(word.get("en", "")).strip(), "en"


def word_mnemonic(word: dict) -> str:
    """巧记文案；兼容字段 mnemonic / 巧记 / qiaoji。"""
    for k in ("mnemonic", "巧记", "qiaoji"):
        v = word.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def spell_answer_line(word: dict) -> str:
    """拼写模式「原文」一行：英文 + 中文义项（若有）。"""
    en = str(word.get("en", "")).strip()
    zh = str(word.get("zh", "")).strip()
    if en and zh:
        return f"{en} ｜ {zh}"
    return en or zh


def normalize_spell_text(s: str) -> str:
    """拼写比对：去首尾空白、折叠空白、英文小写。"""
    t = str(s or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t.casefold()


def spell_attempt_matches_word(word: dict, attempt: str) -> bool:
    en = normalize_spell_text(str(word.get("en", "")).strip())
    if not en:
        return False
    return normalize_spell_text(attempt) == en


def append_wrong_spell_entries(
    rows: list[dict],
    *,
    unit: str,
    lesson: str,
) -> None:
    """
    将错题追加写入 wrong_spell_book.json（供日后「错题回顾」读取）。
    每项含 en, zh, attempt, ts, unit, lesson。
    """
    if not rows:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing: list = []
    if WRONG_SPELL_BOOK_FILE.is_file():
        try:
            raw = json.loads(WRONG_SPELL_BOOK_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                existing = raw
        except Exception:
            existing = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        en = str(r.get("en", "")).strip()
        zh = str(r.get("zh", "")).strip()
        attempt = str(r.get("attempt", "")).strip()
        if not en and not attempt:
            continue
        existing.append(
            {
                "en": en,
                "zh": zh,
                "attempt": attempt,
                "ts": ts,
                "unit": unit,
                "lesson": lesson,
            }
        )
    WRONG_SPELL_BOOK_FILE.parent.mkdir(parents=True, exist_ok=True)
    WRONG_SPELL_BOOK_FILE.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def spell_hint_segment(word: dict, click_index: int) -> tuple[str, str]:
    """
    电脑拼写模式提示：第 click_index 次点击（从 1 起）在巧记与原文之间交替。
    返回 (展示文案, kind)，kind 为 "mnemonic" | "answer" | "mnemonic_empty"。
    """
    m = word_mnemonic(word)
    ans = spell_answer_line(word)
    if click_index % 2 == 1:
        if m:
            return m, "mnemonic"
        return "暂无巧记", "mnemonic_empty"
    return ans, "answer"


def hint_text_and_language(word: dict, dictation_mode: str) -> tuple[str, str]:
    if dictation_mode == "zh_to_en":
        return str(word.get("en", "")).strip(), "en"
    if dictation_mode == "en_spell":
        # 网页端拼写模式用 spell_hint_segment，此处勿用于 TTS
        return "", "en"
    return chinese_speech_text(word), "zh"


def load_last_progress_text() -> str:
    record = get_last_progress_record()
    if not record:
        return "上次听写：暂无记录"
    try:
        unit = str(record.get("unit", "")).strip()
        lesson = str(record.get("lesson", "")).strip()
        word_en = str(record.get("word_en", "")).strip()
        word_zh = str(record.get("word_zh", "")).strip()

        if unit and lesson:
            base = f"上次听写：{unit} / {lesson}"
        elif unit:
            base = f"上次听写：{unit}"
        else:
            base = "上次听写："

        if word_en or word_zh:
            if word_en and word_zh:
                return f"{base}（单词：{word_en} / {word_zh}）"
            if word_en:
                return f"{base}（单词：{word_en}）"
            return f"{base}（单词：{word_zh}）"

        return base if (unit or lesson) else "上次听写：暂无记录"
    except Exception:
        return "上次听写：暂无记录"


def _load_progress_store() -> dict:
    """Return {'last': record|None, 'history': list[record]} with legacy compatibility."""
    if not PROGRESS_FILE.is_file():
        return {"last": None, "history": []}
    try:
        raw = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"last": None, "history": []}

    if isinstance(raw, dict) and ("last" in raw or "history" in raw):
        last = raw.get("last")
        history = raw.get("history")
        if not isinstance(history, list):
            history = []
        return {"last": last if isinstance(last, dict) else None, "history": history}

    # Legacy format: {unit, lesson, ...}
    if isinstance(raw, dict):
        return {"last": raw, "history": [raw]}
    return {"last": None, "history": []}


def _save_progress_store(store: dict) -> None:
    PROGRESS_FILE.write_text(
        json.dumps(store, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_last_progress_record() -> dict | None:
    store = _load_progress_store()
    last = store.get("last")
    return last if isinstance(last, dict) else None


def get_progress_history(limit: int = 10) -> list[dict]:
    store = _load_progress_store()
    history = store.get("history") or []
    if not isinstance(history, list):
        return []
    return [h for h in history if isinstance(h, dict)][: max(0, int(limit))]


def _progress_records_equal(a: dict, b: dict) -> bool:
    """判断两条进度记录是否视为同一条（用于删除历史后是否更新 last）。"""
    keys = ("ts", "word_en", "word_zh", "unit", "lesson", "status", "index")
    for k in keys:
        if str(a.get(k, "")).strip() != str(b.get(k, "")).strip():
            return False
    return True


def delete_progress_history_item(history_index: int) -> tuple[bool, str]:
    """
    按下标删除 progress.json 中 history 的一条。
    若删除项与 last 相同，则将 last 更新为剩余历史中最新一条或 None。
    """
    if history_index < 0:
        return False, "history_index 无效"
    store = _load_progress_store()
    history = store.get("history") or []
    if not isinstance(history, list):
        return False, "无历史记录"
    if history_index >= len(history):
        return False, "历史记录不存在"
    removed = history.pop(history_index)
    if not isinstance(removed, dict):
        return False, "历史记录不存在"
    last = store.get("last")
    if isinstance(last, dict) and _progress_records_equal(last, removed):
        store["last"] = history[0] if history else None
    store["history"] = history
    _save_progress_store(store)
    return True, ""


def save_last_progress(
    unit: str,
    lesson: str,
    word: dict | None = None,
    index: int | None = None,
    status: str = "completed",
) -> None:
    # 保持兼容：桌面版完成时可能只传 unit/lesson；退出时会传 word。
    if unit == "全部单元" and lesson == "全部部分" and word is None:
        return

    payload: dict[str, object] = {
        "unit": unit if unit != "全部单元" else "",
        "lesson": lesson if lesson != "全部部分" else "",
        "status": status,
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    if index is not None:
        payload["index"] = int(index)
    if word:
        en = str(word.get("en", "")).strip()
        zh = str(word.get("zh", "")).strip()
        if en:
            payload["word_en"] = en
        if zh:
            payload["word_zh"] = zh

    store = _load_progress_store()
    history = store.get("history") or []
    if not isinstance(history, list):
        history = []
    history = [payload] + [h for h in history if isinstance(h, dict)]
    store["last"] = payload
    store["history"] = history[:10]
    _save_progress_store(store)


def save_words_to_disk(words: list[dict]) -> None:
    WORDS_FILE.write_text(
        json.dumps(words, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def dedupe_key(entry: dict) -> str:
    """Duplicate only if same (en + pos + unit + lesson)."""
    en = str(entry.get("en", "")).strip().lower()
    pos = str(entry.get("pos", "")).strip().lower()
    unit = str(entry.get("unit", "")).strip().lower()
    lesson = str(entry.get("lesson", "")).strip().lower()
    return f"{en}\t{pos}\t{unit}\t{lesson}"


def normalize_word_entry(item: dict) -> dict | None:
    en = str(item.get("en", "")).strip()
    zh = str(item.get("zh", "")).strip()
    if not en or not zh:
        return None
    out: dict[str, str] = {"en": en, "zh": zh}
    pos = str(item.get("pos", "")).strip()
    if pos:
        out["pos"] = pos
    unit = str(item.get("unit", "")).strip()
    lesson = str(item.get("lesson", "")).strip()
    if unit:
        out["unit"] = unit
    if lesson:
        out["lesson"] = lesson
    return out


def normalize_and_merge(
    words: list[dict],
    raw_list: list[dict],
    default_unit: str,
    default_lesson: str,
) -> tuple[list[dict], int, int]:
    """Merge normalized items; dedupe by (en + pos + unit + lesson). Returns (new_list, added, skipped)."""
    seen = {dedupe_key(w) for w in words}
    out = list(words)
    added = skipped = 0
    for item in raw_list:
        norm = normalize_word_entry(item)
        if not norm:
            continue
        if not str(norm.get("unit", "")).strip():
            norm["unit"] = default_unit
        if not str(norm.get("lesson", "")).strip():
            norm["lesson"] = default_lesson
        key = dedupe_key(norm)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        out.append(norm)
        added += 1
    return out, added, skipped


def default_unit_lesson_for_import(selected_unit: str, selected_lesson: str) -> tuple[str, str]:
    """Match desktop: 全部→Unit 1 / Lesson 1."""
    u = selected_unit if selected_unit != "全部单元" else "Unit 1"
    le = selected_lesson if selected_lesson != "全部部分" else "Lesson 1"
    return u, le
