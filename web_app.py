# -*- coding: utf-8 -*-
"""Web UI for English dictation — open in browser, click to start."""

from __future__ import annotations

import os
import re
import secrets
from functools import wraps

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import dictation_core as dc
from word_import import parse_batch_text, parse_csv_text

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _app_secret_key() -> str:
    """稳定会话密钥：优先环境变量，否则读写项目目录下 .flask_secret_key（避免每次重启导致 Cookie 全部失效）。"""
    env = (os.environ.get("FLASK_SECRET_KEY") or "").strip()
    if env:
        return env
    key_path = os.path.join(_BASE_DIR, ".flask_secret_key")
    try:
        with open(key_path, encoding="utf-8") as f:
            k = f.read().strip()
            if k:
                return k
    except OSError:
        pass
    k = secrets.token_hex(32)
    try:
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(k)
    except OSError:
        pass
    return k


def _web_password() -> str:
    """访问密码：环境变量 DICTATION_WEB_PASSWORD；未设置时可放 web_access_password.txt（单行）。"""
    p = os.environ.get("DICTATION_WEB_PASSWORD", "").strip()
    if p:
        return p
    path = os.path.join(_BASE_DIR, "web_access_password.txt")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


app = Flask(__name__)
app.secret_key = _app_secret_key()
# 改模板后无需重启即可生效（debug=False 时默认会长期缓存模板）
app.config["TEMPLATES_AUTO_RELOAD"] = True


def _login_required():
    pwd = _web_password()
    if not pwd:
        return True
    return session.get("dictation_ok") is True


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not _login_required():
            if request.path.startswith("/api/"):
                return jsonify({"error": "未登录"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)

    return wrapped


def _session_defaults():
    session.setdefault("unit", "全部单元")
    session.setdefault("lesson", "全部部分")
    session.setdefault("mode", "manual")
    session.setdefault("dictation_mode", "en_to_zh")
    session.setdefault("interval", 10)
    session.setdefault("index", 0)
    session.setdefault("session_active", False)
    session.setdefault("auto_paused", False)


@app.route("/login", methods=["GET", "POST"])
def login_page():
    pwd_env = _web_password()
    if not pwd_env:
        session["dictation_ok"] = True
        return redirect(url_for("index"))

    if request.method == "POST":
        form_pwd = (request.form.get("password") or "").strip()
        if form_pwd == pwd_env:
            session["dictation_ok"] = True
            session.permanent = True
            return redirect(url_for("index"))
        return render_template("login.html", error="密码错误")

    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.pop("dictation_ok", None)
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def index():
    show_logout = bool(_web_password())
    return render_template("index.html", show_logout=show_logout)


@app.get("/api/config")
@login_required
def api_config():
    words = dc.load_words_from_disk()
    unit = session.get("unit", "全部单元")
    lesson = session.get("lesson", "全部部分")
    units = ["全部单元"] + dc.list_units(words)
    lessons = ["全部部分"] + dc.list_lessons(words, unit)
    return jsonify(
        {
            "units": units,
            "lessons": lessons,
            "current_library_label": "当前词库（words.json）",
            "unit": unit,
            "lesson": lesson,
            "mode": session.get("mode", "manual"),
            "dictation_mode": session.get("dictation_mode", "en_to_zh"),
            "interval": session.get("interval", 10),
            "last_progress": dc.load_last_progress_text(),
            "last_record": dc.get_last_progress_record(),
            "history": dc.get_progress_history(10),
            "word_count": len(dc.scope_filtered_words(words, unit, lesson)),
        }
    )


@app.post("/api/settings")
@login_required
def api_settings():
    _session_defaults()
    data = request.get_json(force=True, silent=True) or {}
    if "unit" in data:
        session["unit"] = str(data["unit"])
    if "lesson" in data:
        session["lesson"] = str(data["lesson"])
    if "mode" in data and data["mode"] in ("manual", "auto"):
        session["mode"] = data["mode"]
    if "dictation_mode" in data and data["dictation_mode"] in ("en_to_zh", "zh_to_en"):
        session["dictation_mode"] = data["dictation_mode"]
    if "interval" in data:
        try:
            iv = int(data["interval"])
            if iv > 0:
                session["interval"] = iv
        except (TypeError, ValueError):
            pass
    words = dc.load_words_from_disk()
    unit = session.get("unit", "全部单元")
    lesson = session.get("lesson", "全部部分")
    return jsonify(
        {
            "ok": True,
            "lessons": ["全部部分"] + dc.list_lessons(words, unit),
            "word_count": len(dc.scope_filtered_words(words, unit, lesson)),
        }
    )


@app.get("/api/audio/<digest>.mp3")
@login_required
def serve_audio(digest: str):
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        abort(404)
    path = dc.TTS_CACHE_DIR / f"{digest}.mp3"
    if not path.is_file():
        abort(404)
    from flask import send_file

    return send_file(path, mimetype="audio/mpeg")


@app.post("/api/tts")
@login_required
def api_tts():
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("text", "")).strip()
    lang = str(data.get("lang", "en"))
    if lang not in ("en", "zh"):
        lang = "en"
    if not text:
        return jsonify({"error": "empty text"}), 400
    path = dc.ensure_tts_mp3(text, lang)
    digest = path.stem
    url = url_for("serve_audio", digest=digest)
    return jsonify({"url": url})


def _current_filtered():
    words = dc.load_words_from_disk()
    unit = session.get("unit", "全部单元")
    lesson = session.get("lesson", "全部部分")
    return dc.scope_filtered_words(words, unit, lesson)


def _prompt_urls(word: dict) -> list[str]:
    dm = session.get("dictation_mode", "en_to_zh")
    text, lang = dc.prompt_text_and_language(word, dm)
    if not text:
        return []
    path = dc.ensure_tts_mp3(text, lang)
    digest = path.stem
    u = url_for("serve_audio", digest=digest)
    return [u, u]


def _resume_payload_from_record(record: dict) -> tuple[dict | None, str | None]:
    """Resolve saved record to current word index and audio payload."""
    unit = str(record.get("unit", "")).strip() or "全部单元"
    lesson = str(record.get("lesson", "")).strip() or "全部部分"
    session["unit"] = unit
    session["lesson"] = lesson

    filtered = _current_filtered()
    if not filtered:
        return None, "原听写范围已无单词，无法继续。"

    word_en = str(record.get("word_en", "")).strip()
    word_zh = str(record.get("word_zh", "")).strip()
    resolved = None
    if word_en or word_zh:
        for i, w in enumerate(filtered):
            en_ok = (not word_en) or (str(w.get("en", "")).strip() == word_en)
            zh_ok = (not word_zh) or (str(w.get("zh", "")).strip() == word_zh)
            if en_ok and zh_ok:
                resolved = i
                break

    warning = ""
    if resolved is None:
        resolved = 0
        warning = "单词位置变化了，没法按照原来的顺序继续听写。已从该部分第 1 个单词开始。"

    session["index"] = resolved
    session["session_active"] = True
    session["auto_paused"] = False
    word = filtered[resolved]
    urls = _prompt_urls(word)
    payload = {
        "ok": True,
        "index": resolved,
        "total": len(filtered),
        "status": f"第 {resolved + 1} 个单词",
        "audio_urls": urls,
        "warning": warning,
        "unit": unit,
        "lesson": lesson,
    }
    return payload, None


@app.post("/api/start")
@login_required
def api_start():
    _session_defaults()
    data = request.get_json(force=True, silent=True) or {}
    if "unit" in data:
        session["unit"] = str(data["unit"])
    if "lesson" in data:
        session["lesson"] = str(data["lesson"])
    if "mode" in data and data["mode"] in ("manual", "auto"):
        session["mode"] = data["mode"]
    if "dictation_mode" in data and data["dictation_mode"] in ("en_to_zh", "zh_to_en"):
        session["dictation_mode"] = data["dictation_mode"]
    if "interval" in data:
        try:
            iv = int(data["interval"])
            if iv <= 0:
                return jsonify({"error": "间隔必须是正整数"}), 400
            session["interval"] = iv
        except (TypeError, ValueError):
            return jsonify({"error": "间隔必须是正整数"}), 400

    filtered = _current_filtered()
    if not filtered:
        return jsonify({"error": "当前范围没有单词"}), 400

    session["index"] = 0
    session["session_active"] = True
    session["auto_paused"] = False
    word = filtered[0]
    urls = _prompt_urls(word)
    total = len(filtered)
    return jsonify(
        {
            "ok": True,
            "index": 0,
            "total": total,
            "status": f"第 1 个单词",
            "audio_urls": urls,
            "mode": session.get("mode"),
        }
    )


@app.post("/api/next")
@login_required
def api_next():
    _session_defaults()
    if not session.get("session_active", False):
        return jsonify({"error": "已退出或未开始听写，请点击「开始」重新开始。"}), 400
    filtered = _current_filtered()
    if not filtered:
        return jsonify({"error": "没有单词"}), 400
    idx = session.get("index", 0)
    unit = session.get("unit", "全部单元")
    lesson = session.get("lesson", "全部部分")

    if idx >= len(filtered) - 1:
        dc.save_last_progress(unit, lesson, status="completed")
        session["session_active"] = False
        return jsonify(
            {
                "ok": True,
                "done": True,
                "status": "全部完成。",
                "audio_urls": [],
            }
        )

    session["index"] = idx + 1
    word = filtered[session["index"]]
    urls = _prompt_urls(word)
    return jsonify(
        {
            "ok": True,
            "done": False,
            "index": session["index"],
            "total": len(filtered),
            "status": f"第 {session['index'] + 1} 个单词",
            "audio_urls": urls,
        }
    )


@app.post("/api/replay")
@login_required
def api_replay():
    _session_defaults()
    filtered = _current_filtered()
    idx = session.get("index", 0)
    if not filtered or not (0 <= idx < len(filtered)):
        return jsonify({"error": "请先开始听写"}), 400
    word = filtered[idx]
    urls = _prompt_urls(word)
    return jsonify({"audio_urls": urls, "status": f"第 {idx + 1} 个单词"})


@app.post("/api/hint")
@login_required
def api_hint():
    _session_defaults()
    filtered = _current_filtered()
    idx = session.get("index", 0)
    if not filtered or not (0 <= idx < len(filtered)):
        return jsonify({"error": "请先开始听写"}), 400
    word = filtered[idx]
    dm = session.get("dictation_mode", "en_to_zh")
    text, lang = dc.hint_text_and_language(word, dm)
    if not text:
        return jsonify({"error": "无提示内容"}), 400
    path = dc.ensure_tts_mp3(text, lang)
    url = url_for("serve_audio", digest=path.stem)
    return jsonify({"audio_urls": [url]})


@app.post("/api/test-voice")
@login_required
def api_test_voice():
    urls = []
    for text, lang in (
        ("Hello, this is English.", "en"),
        ("你好，这是中文试听。", "zh"),
    ):
        path = dc.ensure_tts_mp3(text, lang)
        urls.append(url_for("serve_audio", digest=path.stem))
    return jsonify({"audio_urls": urls})


@app.post("/api/pause")
@login_required
def api_pause():
    _session_defaults()
    if session.get("mode") != "auto":
        return jsonify({"error": "仅自动模式可用暂停"}), 400
    session["auto_paused"] = not session.get("auto_paused", False)
    return jsonify({"paused": session["auto_paused"]})


@app.post("/api/exit")
@login_required
def api_exit():
    _session_defaults()
    filtered = _current_filtered()
    idx = session.get("index", 0)
    unit = session.get("unit", "全部单元")
    lesson = session.get("lesson", "全部部分")

    word = None
    if filtered and 0 <= idx < len(filtered):
        word = filtered[idx]

    dc.save_last_progress(unit, lesson, word=word, index=idx, status="exited")
    session["session_active"] = False
    session["auto_paused"] = True

    word_en = str(word.get("en", "")).strip() if word else ""
    word_zh = str(word.get("zh", "")).strip() if word else ""

    status = "已退出（已记忆当前单词）" if word_en or word_zh else "已退出。"
    return jsonify(
        {
            "ok": True,
            "status": status,
            "index": idx,
            "total": len(filtered),
            "word_en": word_en,
            "word_zh": word_zh,
            "last_progress": dc.load_last_progress_text(),
        }
    )


@app.route("/api/resume", methods=["GET", "POST"])
@login_required
def api_resume():
    _session_defaults()
    record = dc.get_last_progress_record()
    if not record:
        return jsonify({"error": "暂无可继续的听写记录"}), 400
    payload, err = _resume_payload_from_record(record)
    if err:
        return jsonify({"error": err}), 400
    return jsonify(payload)


@app.route("/api/resume/history", methods=["GET", "POST"])
@app.route("/api/resume-history", methods=["GET", "POST"])
@login_required
def api_resume_history():
    _session_defaults()
    data = request.get_json(force=True, silent=True) or {}
    if not data:
        # GET fallback: /api/resume/history?history_index=0
        data = {"history_index": request.args.get("history_index", -1)}
    try:
        history_index = int(data.get("history_index", -1))
    except (TypeError, ValueError):
        return jsonify({"error": "history_index 无效"}), 400
    history = dc.get_progress_history(10)
    if history_index < 0 or history_index >= len(history):
        return jsonify({"error": "历史记录不存在"}), 400
    payload, err = _resume_payload_from_record(history[history_index])
    if err:
        return jsonify({"error": err}), 400
    return jsonify(payload)


@app.post("/api/words/single")
@login_required
def api_words_single():
    _session_defaults()
    data = request.get_json(force=True, silent=True) or {}
    en = str(data.get("en", "")).strip()
    zh = str(data.get("zh", "")).strip()
    pos = str(data.get("pos", "")).strip()
    if not en or not zh:
        return jsonify({"error": "请填写英文和中文"}), 400
    item: dict[str, str] = {"en": en, "zh": zh}
    if pos:
        item["pos"] = pos
    words = dc.load_words_from_disk()
    du, dl = dc.default_unit_lesson_for_import(
        session.get("unit", "全部单元"),
        session.get("lesson", "全部部分"),
    )
    new_words, added, skipped = dc.normalize_and_merge(words, [item], du, dl)
    if not added:
        return jsonify(
            {
                "ok": True,
                "added": 0,
                "skipped": skipped,
                "message": "相同的「英文 + 词性 + 单元 + 部分」已在词库中，未重复添加。",
            }
        )
    dc.save_words_to_disk(new_words)
    return jsonify({"ok": True, "added": added, "skipped": skipped})


@app.post("/api/words/batch")
@login_required
def api_words_batch():
    _session_defaults()
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("text", ""))
    parsed, bad = parse_batch_text(text)
    items = []
    for p in parsed:
        n = dc.normalize_word_entry(p)
        if n:
            items.append(n)
    if bad and not items:
        return jsonify({"error": "没有解析到有效行，请检查格式（例：abandon,v,放弃;抛弃）"}), 400
    words = dc.load_words_from_disk()
    du, dl = dc.default_unit_lesson_for_import(
        session.get("unit", "全部单元"),
        session.get("lesson", "全部部分"),
    )
    new_words, added, skipped = dc.normalize_and_merge(words, items, du, dl)
    dc.save_words_to_disk(new_words)
    return jsonify(
        {
            "ok": True,
            "added": added,
            "skipped": skipped,
            "bad_lines": len(bad),
        }
    )


@app.post("/api/words/csv")
@login_required
def api_words_csv():
    _session_defaults()
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "请选择 CSV 文件"}), 400
    try:
        raw = f.read().decode("utf-8-sig")
    except Exception as exc:
        return jsonify({"error": f"读取失败：{exc}"}), 400
    items, err_lines = parse_csv_text(raw)
    norms = []
    for it in items:
        n = dc.normalize_word_entry(it)
        if n:
            norms.append(n)
    if not norms:
        return jsonify({"error": "未解析到有效行，请检查编码为 UTF-8，列是否为 英文+中文。"}), 400
    words = dc.load_words_from_disk()
    du, dl = dc.default_unit_lesson_for_import(
        session.get("unit", "全部单元"),
        session.get("lesson", "全部部分"),
    )
    new_words, added, skipped = dc.normalize_and_merge(words, norms, du, dl)
    dc.save_words_to_disk(new_words)
    return jsonify(
        {
            "ok": True,
            "added": added,
            "skipped": skipped,
            "err_lines": len(err_lines),
        }
    )


if __name__ == "__main__":
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(host=host, port=port, debug=False)
