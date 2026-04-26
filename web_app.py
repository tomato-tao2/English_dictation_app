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
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import dictation_core as dc
import user_store as us
from word_import import parse_batch_text, parse_csv_text

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

USE_USER_ACCOUNTS = os.environ.get("USE_USER_ACCOUNTS", "1").strip().lower() in (
    "1",
    "true",
    "yes",
)
if USE_USER_ACCOUNTS:
    us.init_db()

_DICTATION_MODES = ("en_to_zh", "zh_to_en", "en_spell")

# 拼写会话逐词统计（仅内存；键为 session 内 spell_log_id，避免大列表进 Cookie）
SPELL_SESSION_BUFFERS: dict[str, list] = {}
QUIZ_SESSION_BUFFERS: dict[str, dict] = {}


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


def _nav_show_logout() -> bool:
    return bool(_web_password()) or (
        USE_USER_ACCOUNTS and session.get("user_id") is not None
    )


def mark_user_study_dirty() -> None:
    if USE_USER_ACCOUNTS and session.get("user_id"):
        g.user_study_dirty = True


def _flush_study_to_db() -> None:
    if not USE_USER_ACCOUNTS or not session.get("user_id"):
        return
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(us.get_engine()) as db:
        us.persist_staging_to_db(db, int(session["user_id"]))
        db.commit()


def _ensure_user_staging_from_db(user_id: int) -> None:
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(us.get_engine()) as db:
        study = db.get(us.UserStudyData, int(user_id))
        us.write_staging_files(int(user_id), study)


@app.context_processor
def _inject_account_nav():
    return {
        "use_user_accounts": USE_USER_ACCOUNTS,
        "current_username": session.get("user_username"),
    }


@app.before_request
def _bind_user_data_paths():
    if not USE_USER_ACCOUNTS:
        dc.clear_web_data_paths()
        return
    uid = session.get("user_id")
    if not uid:
        dc.clear_web_data_paths()
        return
    staging = us.user_staging_dir(int(uid))
    staging.mkdir(parents=True, exist_ok=True)
    if not (staging / "progress.json").is_file():
        _ensure_user_staging_from_db(int(uid))
    dc.set_web_data_paths(
        progress=staging / "progress.json",
        wrong_spell=staging / "wrong_spell_book.json",
        words_current=staging / "words.json",
    )


@app.after_request
def _after_request_persist_user_study(response):
    if getattr(g, "user_study_dirty", False):
        _flush_study_to_db()
        g.user_study_dirty = False
    return response


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


def _safe_next_url(default: str | None = None) -> str:
    """仅允许站内相对路径，避免开放跳转。"""
    target = str(request.values.get("next", "") or "").strip()
    if target.startswith("/") and not target.startswith("//"):
        return target
    return default or url_for("index", view="app")


def _import_requires_account() -> bool:
    """已开启多用户时，录入单词需登录（写入个人词库）。"""
    return bool(USE_USER_ACCOUNTS) and not session.get("user_id")


def _word_import_account_denied() -> tuple | None:  # returns (Response, status) or None
    """未登录调录入相关 API 时返回 JSON 错误。"""
    if not _import_requires_account():
        return None
    return (
        jsonify(
            {
                "error": "请先登录/注册再使用录入单词功能。",
                "code": "account_required",
            }
        ),
        403,
    )


def _session_defaults():
    session.setdefault("unit", "全部单元")
    session.setdefault("lesson", "全部部分")
    session.setdefault("mode", "manual")
    session.setdefault("dictation_mode", "en_to_zh")
    session.setdefault("interval", 10)
    session.setdefault("index", 0)
    session.setdefault("session_active", False)
    session.setdefault("auto_paused", False)
    session.setdefault("library_id", "current")
    session["library_id"] = dc.normalize_library_id(session.get("library_id"))


def _active_words_path():
    _session_defaults()
    return dc.words_path_for_library_id(session.get("library_id"))


def _session_load_words() -> list[dict]:
    return dc.load_words_from_path(_active_words_path())


def _session_save_words(words: list[dict]) -> None:
    dc.save_words_to_path(_active_words_path(), words)


def _reset_session_on_library_switch() -> None:
    session["unit"] = "全部单元"
    session["lesson"] = "全部部分"
    session["index"] = 0
    session["session_active"] = False
    session["auto_paused"] = False
    session.pop("spell_last_attempt", None)
    session.pop("spell_last_correct", None)
    session.pop("spell_hint_clicks", None)
    session.pop("zh_sense_seq", None)
    _clear_quiz_session()
    _clear_spell_session_log()


@app.route("/login", methods=["GET", "POST"])
def login_page():
    pwd_env = _web_password()
    next_url = _safe_next_url(url_for("index", view="app"))

    if USE_USER_ACCOUNTS:
        if request.method == "GET" and session.get("user_id") and (
            not pwd_env or session.get("dictation_ok")
        ):
            return redirect(next_url)

        if request.method == "POST":
            if pwd_env:
                site_pwd = (request.form.get("site_password") or "").strip()
                if site_pwd != pwd_env:
                    return render_template(
                        "login.html",
                        error="站点访问密码错误",
                        site_password_required=bool(pwd_env),
                        next_url=next_url,
                    )
                session["dictation_ok"] = True

            account = (request.form.get("account") or "").strip()
            user_pwd = request.form.get("user_password") or ""
            from sqlalchemy.orm import Session as OrmSession

            with OrmSession(us.get_engine()) as db:
                user = us.verify_user(db, account, user_pwd)
                if not user:
                    return render_template(
                        "login.html",
                        error="账号或密码错误",
                        site_password_required=bool(pwd_env),
                        next_url=next_url,
                    )
                study = db.get(us.UserStudyData, user.id)
                us.write_staging_files(user.id, study)
                uid = int(user.id)
                uname = str(user.username)

            session["user_id"] = uid
            session["user_username"] = uname
            session.permanent = True
            if not pwd_env:
                session["dictation_ok"] = True
            return redirect(next_url)

        return render_template(
            "login.html",
            error=None,
            site_password_required=bool(pwd_env),
            next_url=next_url,
        )

    if not pwd_env:
        session["dictation_ok"] = True
        return redirect(url_for("index"))

    if request.method == "POST":
        form_pwd = (request.form.get("password") or "").strip()
        if form_pwd == pwd_env:
            session["dictation_ok"] = True
            session.permanent = True
            return redirect(next_url)
        return render_template(
            "login.html",
            error="密码错误",
            site_password_required=False,
            legacy_site_only=True,
            next_url=next_url,
        )

    return render_template(
        "login.html",
        error=None,
        site_password_required=False,
        legacy_site_only=True,
        next_url=next_url,
    )


@app.route("/register", methods=["GET", "POST"])
def register_page():
    if not USE_USER_ACCOUNTS:
        return redirect(url_for("login_page"))
    pwd_env = _web_password()
    next_url = _safe_next_url(url_for("index", view="app"))
    if request.method == "GET":
        return render_template(
            "register.html",
            error=None,
            site_password_required=bool(pwd_env),
            next_url=next_url,
        )

    if pwd_env:
        site_pwd = (request.form.get("site_password") or "").strip()
        if site_pwd != pwd_env:
            return render_template(
                "register.html",
                error="站点访问密码错误",
                site_password_required=True,
                next_url=next_url,
            )

    username = (request.form.get("username") or "").strip()
    account = (request.form.get("account") or "").strip()
    password = request.form.get("password") or ""
    password2 = request.form.get("password2") or ""
    if password != password2:
        return render_template(
            "register.html",
            error="两次输入的密码不一致",
            site_password_required=bool(pwd_env),
            next_url=next_url,
        )
    from sqlalchemy.orm import Session as OrmSession

    with OrmSession(us.get_engine()) as db:
        user, err = us.create_user(db, username, account, password)
        if not user:
            return render_template(
                "register.html",
                error=err or "注册失败",
                site_password_required=bool(pwd_env),
                next_url=next_url,
            )
        db.commit()
        uid = int(user.id)
        uname = str(user.username)
        study = db.get(us.UserStudyData, uid)
        us.write_staging_files(uid, study)

    # 注册成功后直接登录，避免再走一遍登录流程
    session["user_id"] = uid
    session["user_username"] = uname
    session.permanent = True
    if pwd_env:
        session["dictation_ok"] = True
    return redirect(next_url)


@app.route("/logout")
def logout():
    if USE_USER_ACCOUNTS and session.get("user_id"):
        _flush_study_to_db()
    session.pop("user_id", None)
    session.pop("user_username", None)
    session.pop("dictation_ok", None)
    return redirect(url_for("index", view="app"))


@app.route("/")
@login_required
def index():
    show_logout = _nav_show_logout()
    return render_template("index.html", show_logout=show_logout)


@app.route("/dictation")
@app.route("/dictation/")
@app.route("/tingxie")
@app.route("/word-dictation")
@login_required
def dictation_page():
    """英语听写独立页（设置与操作见 templates/dictation.html）。多路径避免个别环境路由异常。"""
    show_logout = _nav_show_logout()
    return render_template("dictation.html", show_logout=show_logout)


@app.route("/quiz")
@app.route("/quiz/")
@login_required
def quiz_page():
    show_logout = _nav_show_logout()
    return render_template("quiz.html", show_logout=show_logout)


@app.get("/_debug/who")
def debug_who():
    """本机自查：当前进程是否为本项目、是否注册听写路由（勿在生产环境暴露公网）。"""
    addr = (request.remote_addr or "").split("%")[0]
    if addr not in ("127.0.0.1", "::1", "localhost"):
        abort(404)
    rules = sorted(
        f"{r.rule!s}  ->  {r.endpoint}" for r in app.url_map.iter_rules()
    )
    has_dictation = any(
        "/dictation" in str(r.rule) or r.endpoint == "dictation_page"
        for r in app.url_map.iter_rules()
    )
    try:
        mtime = os.path.getmtime(os.path.abspath(__file__))
    except OSError:
        mtime = None
    return jsonify(
        {
            "ok": True,
            "message": "若 has_dictation 为 false，说明运行的不是最新 web_app.py",
            "has_dictation_route": has_dictation,
            "web_app_py_mtime": mtime,
            "registered_rules": rules,
        }
    )


@app.route("/import")
@login_required
def import_page():
    show_logout = _nav_show_logout()
    if _import_requires_account():
        login_with_next = url_for("login_page", next="/import")
        return render_template(
            "import_login_gate.html",
            show_logout=show_logout,
            login_with_next_url=login_with_next,
        )
    return render_template("import.html", show_logout=show_logout)


@app.route("/wrong-review")
@login_required
def wrong_review_page():
    show_logout = _nav_show_logout()
    rows_dictation = dc.load_wrong_spell_entries(
        300, source=dc.WRONG_SOURCE_DICTATION_SPELL
    )
    rows_quiz = dc.load_wrong_spell_entries(300, source=dc.WRONG_SOURCE_QUIZ)
    return render_template(
        "wrong_review.html",
        show_logout=show_logout,
        rows_dictation=rows_dictation,
        rows_quiz=rows_quiz,
    )


@app.get("/api/config")
@login_required
def api_config():
    _session_defaults()
    active_path = _active_words_path()
    words = dc.load_words_from_path(active_path)
    unit = session.get("unit", "全部单元")
    lesson = session.get("lesson", "全部部分")
    units = ["全部单元"] + dc.list_units(words)
    lessons = ["全部部分"] + dc.list_lessons(words, unit)
    lid = session.get("library_id", "current")
    libraries_meta = []
    for sid, label, _fname in dc.LIBRARY_ENTRIES:
        p = dc.words_path_for_library_id(sid)
        exists = p.is_file()
        if p == active_path:
            wc = len(words)
        elif exists:
            wc = len(dc.load_words_from_path(p))
        else:
            wc = 0
        libraries_meta.append(
            {
                "id": sid,
                "label": label,
                "exists": exists,
                "word_count": wc,
            }
        )
    return jsonify(
        {
            "units": units,
            "lessons": lessons,
            "library_id": lid,
            "libraries": libraries_meta,
            "current_library_label": dc.library_label_for_id(lid),
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
    if "dictation_mode" in data and data["dictation_mode"] in _DICTATION_MODES:
        session["dictation_mode"] = data["dictation_mode"]
    if "interval" in data:
        try:
            iv = int(data["interval"])
            if iv > 0:
                session["interval"] = iv
        except (TypeError, ValueError):
            pass
    words = _session_load_words()
    unit = session.get("unit", "全部单元")
    lesson = session.get("lesson", "全部部分")
    return jsonify(
        {
            "ok": True,
            "lessons": ["全部部分"] + dc.list_lessons(words, unit),
            "word_count": len(dc.scope_filtered_words(words, unit, lesson)),
        }
    )


@app.post("/api/library/select")
@login_required
def api_library_select():
    _session_defaults()
    data = request.get_json(force=True, silent=True) or {}
    new_id = str(data.get("library_id", "current")).strip().lower()
    if not dc.is_known_library_id(new_id):
        return jsonify({"error": "未知词库"}), 400
    session["library_id"] = new_id
    _reset_session_on_library_switch()
    return jsonify({"ok": True, "library_id": new_id})


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
    words = _session_load_words()
    unit = session.get("unit", "全部单元")
    lesson = session.get("lesson", "全部部分")
    return dc.scope_filtered_words(words, unit, lesson)


def _reset_spell_hint_clicks() -> None:
    session["spell_hint_clicks"] = 0


def _init_spell_session_log() -> None:
    """开始 / 恢复拼写听写时创建空缓冲。"""
    lid = secrets.token_hex(8)
    session["spell_log_id"] = lid
    SPELL_SESSION_BUFFERS[lid] = []


def _clear_spell_session_log() -> None:
    lid = session.pop("spell_log_id", None)
    if lid:
        SPELL_SESSION_BUFFERS.pop(lid, None)


def _finalize_current_spell_row(filtered: list, idx: int) -> None:
    """离开当前词前写入一行拼写统计（不改变 index）。"""
    if session.get("dictation_mode") != "en_spell":
        return
    log_id = session.get("spell_log_id")
    if not log_id or not filtered or not (0 <= idx < len(filtered)):
        return
    w = filtered[idx]
    hints = int(session.get("spell_hint_clicks", 0))
    attempt = session.pop("spell_last_attempt", None)
    correct = session.pop("spell_last_correct", None)
    submitted = attempt is not None
    row = {
        "en": str(w.get("en", "")).strip(),
        "zh": dc.format_all_senses_zh(w),
        "hint_count": hints,
        "submitted": submitted,
        "attempt": str(attempt).strip() if submitted else "",
        "correct": bool(correct) if submitted else None,
    }
    SPELL_SESSION_BUFFERS.setdefault(log_id, []).append(row)


def _take_spell_summary_and_clear_buffer() -> dict | None:
    """生成 spell_summary 并移除缓冲；若无 log 或无数据则返回 None。"""
    log_id = session.pop("spell_log_id", None)
    if not log_id:
        return None
    rows = list(SPELL_SESSION_BUFFERS.pop(log_id, None) or [])
    if not rows:
        return None
    answered = sum(1 for r in rows if r.get("submitted"))
    correct_n = sum(1 for r in rows if r.get("submitted") and r.get("correct") is True)
    unanswer = sum(1 for r in rows if not r.get("submitted"))
    hint_used_words = sum(1 for r in rows if int(r.get("hint_count", 0)) > 0)
    wrong_rows = [
        {"en": r["en"], "zh": r["zh"], "attempt": r.get("attempt", "")}
        for r in rows
        if r.get("submitted") and r.get("correct") is False
    ]
    unsubmitted_rows = [{"en": r["en"], "zh": r["zh"]} for r in rows if not r.get("submitted")]
    hint_rows = [
        {"en": r["en"], "zh": r["zh"], "hint_count": int(r.get("hint_count", 0))}
        for r in rows
        if int(r.get("hint_count", 0)) > 0
    ]
    return {
        "stats": {
            "answered": answered,
            "correct": correct_n,
            "unanswered": unanswer,
            "hint_used_words": hint_used_words,
        },
        "wrong_rows": wrong_rows,
        "unsubmitted_rows": unsubmitted_rows,
        "hint_rows": hint_rows,
    }


def _persist_spell_wrongs_and_maybe_append(summary: dict | None) -> None:
    if not summary:
        return
    wrong = summary.get("wrong_rows") or []
    if not wrong:
        return
    unit = str(session.get("unit", "全部单元") or "全部单元")
    lesson = str(session.get("lesson", "全部部分") or "全部部分")
    dc.append_wrong_spell_entries(
        wrong,
        unit=unit,
        lesson=lesson,
        source=dc.WRONG_SOURCE_DICTATION_SPELL,
    )


def _clear_quiz_session() -> None:
    qid = session.pop("quiz_session_id", None)
    if qid:
        QUIZ_SESSION_BUFFERS.pop(qid, None)
    session["quiz_active"] = False
    session.pop("quiz_mode", None)


def _init_quiz_session(pool: list[dict], mode: str) -> None:
    qid = secrets.token_hex(8)
    QUIZ_SESSION_BUFFERS[qid] = {
        "pool": pool,
        "mode": mode,
        "order": list(range(len(pool))),
        "cursor": 0,
        "wrong_rows": [],
        "unknown_rows": [],
    }
    session["quiz_session_id"] = qid
    session["quiz_active"] = True
    session["quiz_mode"] = mode


def _quiz_question_payload(state: dict) -> dict:
    order = state.get("order") or []
    cursor = int(state.get("cursor", 0))
    if cursor >= len(order):
        wrong_rows = list(state.get("wrong_rows") or [])
        unknown_rows = list(state.get("unknown_rows") or [])
        return {
            "ok": True,
            "done": True,
            "status": "本轮完成。",
            "wrong_rows": wrong_rows,
            "unknown_rows": unknown_rows,
        }
    idx = int(order[cursor])
    q = dc.build_quiz_question(
        state.get("pool") or [],
        idx,
        str(state.get("mode") or "en_pick_zh"),
    )
    if "error" in q:
        return {"error": q["error"]}
    state["current"] = q
    payload = {
        "ok": True,
        "done": False,
        "index": cursor + 1,
        "total": len(order),
        "prompt": q.get("prompt", ""),
        "kind": q.get("kind", "choice"),
        "quiz_mode": state.get("mode", "en_pick_zh"),
    }
    if q.get("kind") == "choice":
        payload["options"] = q.get("options") or []
    return payload


def _next_zh_sense_index() -> int:
    session.setdefault("zh_sense_seq", 0)
    v = int(session["zh_sense_seq"])
    session["zh_sense_seq"] = v + 1
    return v


def _prompt_urls(word: dict) -> list[str]:
    dm = session.get("dictation_mode", "en_to_zh")
    sense_i = _next_zh_sense_index() if dm == "zh_to_en" else None
    text, lang = dc.prompt_text_and_language(word, dm, sense_i)
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
            zh_ok = (not word_zh) or dc.word_zh_matches_record(w, word_zh)
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
    _reset_spell_hint_clicks()
    session.pop("spell_last_attempt", None)
    session.pop("spell_last_correct", None)
    _clear_spell_session_log()
    if session.get("dictation_mode") == "en_spell":
        _init_spell_session_log()
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
    if "dictation_mode" in data and data["dictation_mode"] in _DICTATION_MODES:
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
    session["zh_sense_seq"] = 0
    _reset_spell_hint_clicks()
    session.pop("spell_last_attempt", None)
    session.pop("spell_last_correct", None)
    _clear_spell_session_log()
    if session.get("dictation_mode") == "en_spell":
        _init_spell_session_log()
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
        _finalize_current_spell_row(filtered, idx)
        dc.save_last_progress(unit, lesson, status="completed")
        session["session_active"] = False
        spell_summary = _take_spell_summary_and_clear_buffer()
        _persist_spell_wrongs_and_maybe_append(spell_summary)
        mark_user_study_dirty()
        payload = {
            "ok": True,
            "done": True,
            "status": "全部完成。",
            "audio_urls": [],
        }
        if spell_summary:
            payload["spell_summary"] = spell_summary
        return jsonify(payload)

    _finalize_current_spell_row(filtered, idx)
    session["index"] = idx + 1
    _reset_spell_hint_clicks()
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
    if dm == "en_spell":
        session["spell_hint_clicks"] = int(session.get("spell_hint_clicks", 0)) + 1
        c = session["spell_hint_clicks"]
        hint_text, hint_kind = dc.spell_hint_segment(word, c)
        return jsonify(
            {
                "audio_urls": [],
                "hint_text": hint_text,
                "hint_kind": hint_kind,
            }
        )
    text, lang = dc.hint_text_and_language(word, dm)
    if not text:
        return jsonify({"error": "无提示内容"}), 400
    path = dc.ensure_tts_mp3(text, lang)
    url = url_for("serve_audio", digest=path.stem)
    return jsonify({"audio_urls": [url]})


@app.post("/api/spell/submit")
@login_required
def api_spell_submit():
    _session_defaults()
    if not session.get("session_active", False):
        return jsonify({"error": "请先开始听写"}), 400
    if session.get("dictation_mode") != "en_spell":
        return jsonify({"error": "仅拼写模式可用"}), 400
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"error": "请输入拼写后再确认"}), 400
    filtered = _current_filtered()
    idx = session.get("index", 0)
    if not filtered or not (0 <= idx < len(filtered)):
        return jsonify({"error": "没有当前单词"}), 400
    word = filtered[idx]
    ok = dc.spell_attempt_matches_word(word, text)
    session["spell_last_attempt"] = text
    session["spell_last_correct"] = ok
    return jsonify({"ok": True, "recorded": True})


@app.get("/api/quiz/config")
@login_required
def api_quiz_config():
    _session_defaults()
    words = _session_load_words()
    unit = session.get("unit", "全部单元")
    lesson = session.get("lesson", "全部部分")
    scoped = dc.scope_filtered_words(words, unit, lesson)
    eligible = sum(1 for w in scoped if dc.quiz_word_eligible(w))
    return jsonify(
        {
            "unit": unit,
            "lesson": lesson,
            "units": ["全部单元"] + dc.list_units(words),
            "lessons": ["全部部分"] + dc.list_lessons(words, unit),
            "quiz_mode": session.get("quiz_mode", "en_pick_zh"),
            "word_count": eligible,
            "mode_options": list(dc.QUIZ_MODES),
        }
    )


@app.post("/api/quiz/settings")
@login_required
def api_quiz_settings():
    _session_defaults()
    data = request.get_json(force=True, silent=True) or {}
    if "unit" in data:
        session["unit"] = str(data["unit"])
    if "lesson" in data:
        session["lesson"] = str(data["lesson"])
    if "quiz_mode" in data and str(data["quiz_mode"]) in dc.QUIZ_MODES:
        session["quiz_mode"] = str(data["quiz_mode"])
    words = _session_load_words()
    unit = session.get("unit", "全部单元")
    lesson = session.get("lesson", "全部部分")
    scoped = dc.scope_filtered_words(words, unit, lesson)
    eligible = sum(1 for w in scoped if dc.quiz_word_eligible(w))
    return jsonify(
        {
            "ok": True,
            "lessons": ["全部部分"] + dc.list_lessons(words, unit),
            "word_count": eligible,
        }
    )


@app.post("/api/quiz/start")
@login_required
def api_quiz_start():
    _session_defaults()
    data = request.get_json(force=True, silent=True) or {}
    if "unit" in data:
        session["unit"] = str(data["unit"])
    if "lesson" in data:
        session["lesson"] = str(data["lesson"])
    if "quiz_mode" in data and str(data["quiz_mode"]) in dc.QUIZ_MODES:
        session["quiz_mode"] = str(data["quiz_mode"])
    mode = str(session.get("quiz_mode", "en_pick_zh"))
    words = _session_load_words()
    scoped = dc.scope_filtered_words(
        words, session.get("unit", "全部单元"), session.get("lesson", "全部部分")
    )
    pool = [w for w in scoped if dc.quiz_word_eligible(w)]
    need_n = 1 if mode == "en_recall" else 4
    if len(pool) < need_n:
        return jsonify({"error": f"当前范围可抽背单词不足 {need_n} 个"}), 400
    _clear_quiz_session()
    _init_quiz_session(pool, mode)
    state = QUIZ_SESSION_BUFFERS.get(session.get("quiz_session_id", ""), {})
    payload = _quiz_question_payload(state)
    if "error" in payload:
        return jsonify(payload), 400
    return jsonify(payload)


@app.post("/api/quiz/next")
@login_required
def api_quiz_next():
    if not session.get("quiz_active"):
        return jsonify({"error": "请先开始抽背"}), 400
    qid = session.get("quiz_session_id", "")
    state = QUIZ_SESSION_BUFFERS.get(qid)
    if not state:
        return jsonify({"error": "抽背会话已失效，请重新开始"}), 400
    payload = _quiz_question_payload(state)
    if "error" in payload:
        return jsonify(payload), 400
    return jsonify(payload)


@app.post("/api/quiz/submit")
@login_required
def api_quiz_submit():
    if not session.get("quiz_active"):
        return jsonify({"error": "请先开始抽背"}), 400
    qid = session.get("quiz_session_id", "")
    state = QUIZ_SESSION_BUFFERS.get(qid)
    if not state:
        return jsonify({"error": "抽背会话已失效，请重新开始"}), 400
    current = state.get("current") or {}
    if not current:
        return jsonify({"error": "当前无题目，请先请求下一题"}), 400
    data = request.get_json(force=True, silent=True) or {}
    kind = current.get("kind", "choice")
    word = current.get("word") or {}
    is_correct = True
    if kind == "choice":
        try:
            choice_id = int(data.get("option_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "请选择选项"}), 400
        correct_id = int(current.get("correct_id", -1))
        is_correct = choice_id == correct_id
        if not is_correct:
            picked = next(
                (o.get("text", "") for o in (current.get("options") or []) if int(o.get("id", -1)) == choice_id),
                "",
            )
            state.setdefault("wrong_rows", []).append(
                {
                    "en": str(word.get("en", "")).strip(),
                    "zh": dc.format_all_senses_zh(word),
                    "attempt": str(picked).strip(),
                }
            )
    else:
        known = str(data.get("known", "")).strip().lower()
        if known not in ("yes", "no"):
            return jsonify({"error": "请先选择会/不会"}), 400
        is_correct = known == "yes"
        if not is_correct:
            state.setdefault("unknown_rows", []).append(
                {
                    "en": str(word.get("en", "")).strip(),
                    "zh": dc.format_all_senses_zh(word),
                    "attempt": "不会",
                }
            )
    state["cursor"] = int(state.get("cursor", 0)) + 1
    payload = {
        "ok": True,
        "correct": is_correct,
        "explanation": dc.format_quiz_explanation(word),
    }
    if kind == "choice":
        payload["correct_id"] = int(current.get("correct_id", -1))
    return jsonify(payload)


@app.post("/api/quiz/exit")
@login_required
def api_quiz_exit():
    qid = session.get("quiz_session_id", "")
    state = QUIZ_SESSION_BUFFERS.get(qid) or {}
    wrong = list(state.get("wrong_rows") or []) + list(state.get("unknown_rows") or [])
    if wrong:
        dc.append_wrong_spell_entries(
            wrong,
            unit=str(session.get("unit", "全部单元")),
            lesson=str(session.get("lesson", "全部部分")),
            source=dc.WRONG_SOURCE_QUIZ,
        )
        mark_user_study_dirty()
    _clear_quiz_session()
    return jsonify({"ok": True, "saved_wrong": len(wrong)})


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

    if session.get("session_active") and session.get("dictation_mode") == "en_spell":
        _finalize_current_spell_row(filtered, idx)

    dc.save_last_progress(unit, lesson, word=word, index=idx, status="exited")
    session["session_active"] = False
    session["auto_paused"] = True

    word_en = str(word.get("en", "")).strip() if word else ""
    word_zh = dc.format_all_senses_zh(word) if word else ""

    spell_summary = _take_spell_summary_and_clear_buffer()
    _persist_spell_wrongs_and_maybe_append(spell_summary)
    mark_user_study_dirty()

    status = "已退出（已记忆当前单词）" if word_en or word_zh else "已退出。"
    out = {
        "ok": True,
        "status": status,
        "index": idx,
        "total": len(filtered),
        "word_en": word_en,
        "word_zh": word_zh,
        "last_progress": dc.load_last_progress_text(),
    }
    if spell_summary:
        out["spell_summary"] = spell_summary
    return jsonify(out)


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


@app.post("/api/progress/history/delete")
@login_required
def api_progress_history_delete():
    _session_defaults()
    data = request.get_json(force=True, silent=True) or {}
    try:
        history_index = int(data.get("history_index", -1))
    except (TypeError, ValueError):
        return jsonify({"error": "history_index 无效"}), 400
    ok, err = dc.delete_progress_history_item(history_index)
    if not ok:
        return jsonify({"error": err or "删除失败"}), 400
    mark_user_study_dirty()
    return jsonify({"ok": True})


@app.post("/api/words/single")
@login_required
def api_words_single():
    denied = _word_import_account_denied()
    if denied is not None:
        return denied
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
    words = _session_load_words()
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
    _session_save_words(new_words)
    mark_user_study_dirty()
    return jsonify({"ok": True, "added": added, "skipped": skipped})


@app.post("/api/words/batch")
@login_required
def api_words_batch():
    denied = _word_import_account_denied()
    if denied is not None:
        return denied
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
    words = _session_load_words()
    du, dl = dc.default_unit_lesson_for_import(
        session.get("unit", "全部单元"),
        session.get("lesson", "全部部分"),
    )
    new_words, added, skipped = dc.normalize_and_merge(words, items, du, dl)
    _session_save_words(new_words)
    mark_user_study_dirty()
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
    denied = _word_import_account_denied()
    if denied is not None:
        return denied
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
    words = _session_load_words()
    du, dl = dc.default_unit_lesson_for_import(
        session.get("unit", "全部单元"),
        session.get("lesson", "全部部分"),
    )
    new_words, added, skipped = dc.normalize_and_merge(words, norms, du, dl)
    _session_save_words(new_words)
    mark_user_study_dirty()
    return jsonify(
        {
            "ok": True,
            "added": added,
            "skipped": skipped,
            "err_lines": len(err_lines),
        }
    )


if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    print("=" * 56)
    print("英语单词听写  Web 服务")
    print("  首页:   http://%s:%s/" % (host, port))
    print("  听写页: http://%s:%s/dictation （备用: /tingxie）" % (host, port))
    print("  录入页: http://%s:%s/import" % (host, port))
    print("  自查:   http://%s:%s/_debug/who （若听写 404 先打开此项）" % (host, port))
    if not any(r.rule == "/dictation" for r in app.url_map.iter_rules()):
        print("  [错误] 未注册 /dictation，请确认 web_app.py 已保存为最新版本。")
    print("=" * 56)
    app.run(host=host, port=port, debug=False)
