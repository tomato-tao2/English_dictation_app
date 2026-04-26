# -*- coding: utf-8 -*-
"""用户账号与个人学习数据（进度、错题、个人词库）— 支持 SQLite 本地与 PostgreSQL 云上。"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, ForeignKey, String, Text, create_engine, select
from sqlalchemy.engine.url import URL
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_INSTANCE_DIR = os.path.join(_BASE_DIR, "instance")


def database_url() -> str:
    u = (os.environ.get("DATABASE_URL") or "").strip()
    if u:
        if u.startswith("postgres://"):
            u = "postgresql+psycopg://" + u[len("postgres://") :]
        elif u.startswith("postgresql://") and "+psycopg" not in u.split("://", 1)[0]:
            u = "postgresql+psycopg://" + u.split("://", 1)[-1]
        return u
    os.makedirs(_INSTANCE_DIR, exist_ok=True)
    db_path = str((Path(_INSTANCE_DIR).resolve() / "dictation_users.db"))
    return str(URL.create("sqlite", database=db_path))


class Base(DeclarativeBase):
    pass


class UserStudyData(Base):
    __tablename__ = "user_study_data"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    progress_json: Mapped[str] = mapped_column(
        Text, default='{"last": null, "history": []}'
    )
    wrong_spell_json: Mapped[str] = mapped_column(Text, default="[]")
    words_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["User"] = relationship("User", back_populates="study")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80))
    account: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    study: Mapped[UserStudyData | None] = relationship(
        UserStudyData, back_populates="user", uselist=False
    )


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = database_url()
        kw: dict = {"echo": False, "future": True}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kw)
    return _engine


def init_db() -> None:
    """建表。SQLite + 多 Gunicorn worker 时，并发 `create_all` 会竞态并触发 table already exists，故非 Windows 用文件锁串行化 DDL。"""
    eng = get_engine()
    if eng.dialect.name != "sqlite" or sys.platform == "win32":
        Base.metadata.create_all(eng, checkfirst=True)
        return
    os.makedirs(_INSTANCE_DIR, exist_ok=True)
    lock_path = os.path.join(_INSTANCE_DIR, ".init_db.lock")
    import fcntl

    with open(lock_path, "a+b") as lock_fp:
        fcntl.flock(lock_fp, fcntl.LOCK_EX)
        try:
            Base.metadata.create_all(get_engine(), checkfirst=True)
        finally:
            fcntl.flock(lock_fp, fcntl.LOCK_UN)


def _norm_account(s: str) -> str:
    return str(s or "").strip().lower()


def validate_account_fields(
    username: str, account: str, password: str
) -> tuple[bool, str]:
    u = str(username or "").strip()
    a = _norm_account(account)
    p = str(password or "")
    if not u or len(u) > 80:
        return False, "昵称长度为 1～80 个字符。"
    if len(a) < 3 or len(a) > 32:
        return False, "账号长度为 3～32 个字符。"
    if not re.fullmatch(r"[a-z0-9_]+", a):
        return False, "账号只能包含小写英文、数字和下划线。"
    if len(p) < 8 or len(p) > 128:
        return False, "密码长度为 8～128 个字符。"
    return True, ""


def create_user(db: Session, username: str, account: str, password: str) -> tuple[User | None, str]:
    ok, err = validate_account_fields(username, account, password)
    if not ok:
        return None, err
    nick = unicodedata.normalize("NFKC", str(username or "").strip())[:80]
    a = _norm_account(account)
    p = str(password or "")
    exists = db.scalar(select(User).where(User.account == a))
    if exists:
        return None, "该账号已被注册。"
    from werkzeug.security import generate_password_hash

    user = User(
        username=nick,
        account=a,
        password_hash=generate_password_hash(p),
    )
    db.add(user)
    db.flush()
    study = UserStudyData(
        user_id=user.id,
        progress_json=json.dumps({"last": None, "history": []}, ensure_ascii=False),
        wrong_spell_json="[]",
        words_json="[]",
    )
    db.add(study)
    return user, ""


def verify_user(db: Session, account: str, password: str) -> User | None:
    from werkzeug.security import check_password_hash

    a = _norm_account(account)
    user = db.scalar(select(User).where(User.account == a))
    if not user:
        return None
    if check_password_hash(user.password_hash, password):
        return user
    return None


def user_staging_dir(user_id: int) -> Path:
    return Path(_INSTANCE_DIR) / "user_staging" / str(int(user_id))


DEFAULT_PROGRESS = '{"last": null, "history": []}'


def study_payload_from_row(study: UserStudyData | None) -> tuple[str, str, str]:
    if study is None:
        return DEFAULT_PROGRESS, "[]", "[]"
    pj = (study.progress_json or "").strip() or DEFAULT_PROGRESS
    wj = (study.wrong_spell_json or "").strip() or "[]"
    words_j = (study.words_json or "").strip() or "[]"
    return pj, wj, words_j


def write_staging_files(user_id: int, study: UserStudyData | None) -> Path:
    d = user_staging_dir(user_id)
    d.mkdir(parents=True, exist_ok=True)
    pj, wj, words_j = study_payload_from_row(study)
    (d / "progress.json").write_text(
        _pretty_json_if_needed(pj) + "\n", encoding="utf-8"
    )
    (d / "wrong_spell_book.json").write_text(
        _pretty_json_if_needed(wj) + "\n", encoding="utf-8"
    )
    (d / "words.json").write_text(
        _pretty_json_if_needed(words_j) + "\n", encoding="utf-8"
    )
    return d


def _pretty_json_if_needed(raw: str) -> str:
    try:
        obj = json.loads(raw)
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return raw


def read_staging_files_to_strings(user_id: int) -> tuple[str, str, str] | None:
    d = user_staging_dir(user_id)
    pp = d / "progress.json"
    wp = d / "wrong_spell_book.json"
    wwords = d / "words.json"
    if not pp.is_file() and not wp.is_file() and not wwords.is_file():
        return None
    def _read(p: Path, default: str) -> str:
        if not p.is_file():
            return default
        return p.read_text(encoding="utf-8")

    return (
        _read(pp, DEFAULT_PROGRESS),
        _read(wp, "[]"),
        _read(wwords, "[]"),
    )


def persist_staging_to_db(db: Session, user_id: int) -> None:
    triple = read_staging_files_to_strings(user_id)
    if triple is None:
        return
    pj, wj, words_j = triple
    study = db.get(UserStudyData, int(user_id))
    if study is None:
        return
    study.progress_json = _normalize_progress_json(pj)
    study.wrong_spell_json = _normalize_list_json(wj)
    study.words_json = _normalize_list_json(words_j)
    study.updated_at = datetime.now(timezone.utc)


def _normalize_progress_json(s: str) -> str:
    try:
        o = json.loads(s)
        if isinstance(o, dict):
            return json.dumps(o, ensure_ascii=False)
    except Exception:
        pass
    return DEFAULT_PROGRESS


def _normalize_list_json(s: str) -> str:
    try:
        o = json.loads(s)
        if isinstance(o, list):
            return json.dumps(o, ensure_ascii=False)
    except Exception:
        pass
    return "[]"
