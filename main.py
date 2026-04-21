import asyncio
import hashlib
import json
import queue
import re
import threading
import time
from ctypes import c_buffer, windll
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import edge_tts
import dictation_core as dc

from word_import import parse_batch_text, parse_csv_text

SCRIPT_DIR = Path(__file__).resolve().parent
WORDS_FILE = SCRIPT_DIR / "words.json"
PROGRESS_FILE = SCRIPT_DIR / "progress.json"

# 与你试听 mp3 对应的 Edge 神经语音（非播放这两个文件，而是用相同 voice 在线合成）
EDGE_VOICE_EN = "en-US-AriaNeural"
EDGE_VOICE_ZH = "zh-CN-XiaoxiaoNeural"
TTS_CACHE_DIR = SCRIPT_DIR / "tts_cache"


def _play_mp3_blocking_win(path: Path, stop_event: threading.Event | None = None) -> bool:
    """Play MP3 using Windows MCI with stoppable polling loop."""
    p = str(path.resolve())
    buf = c_buffer(255)
    mci = windll.winmm.mciSendStringW
    alias = f"mp3_{threading.get_ident()}_{id(path)}"
    err = mci(f'open "{p}" type mpegvideo alias {alias}', buf, 254, 0)
    if err != 0:
        raise RuntimeError(f"MCI open failed: {buf.value.decode('utf-8', errors='replace')}")
    try:
        err = mci(f"play {alias}", buf, 254, 0)
        if err != 0:
            raise RuntimeError(f"MCI play failed: {buf.value.decode('utf-8', errors='replace')}")
        while True:
            if stop_event and stop_event.is_set():
                mci(f"stop {alias}", buf, 254, 0)
                return False
            mci(f"status {alias} mode", buf, 254, 0)
            mode = buf.value.decode("utf-8", errors="replace").strip().lower()
            if mode in {"stopped", "not ready"}:
                return True
            time.sleep(0.03)
    finally:
        mci(f"close {alias}", buf, 254, 0)


class DictationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("英语单词听写")
        self.root.geometry("600x380")

        self.words = []
        self.index = 0

        self.mode = tk.StringVar(value="manual")
        self.dictation_mode = tk.StringVar(value="en_to_zh")
        self.interval = tk.IntVar(value=10)
        self.selected_unit = tk.StringVar(value="全部单元")
        self.selected_lesson = tk.StringVar(value="全部部分")
        self.last_progress_text = tk.StringVar(value="上次听写：暂无记录")

        self.auto_running = False
        self.auto_paused = False
        self.session_active = False

        self.speech_queue = queue.Queue()
        self.speech_running = False
        self.filtered_words: list[dict] = []
        self.stop_playback_event = threading.Event()

        TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        self._build_ui()
        self._load_words()
        self._refresh_scope_selectors()
        self._load_last_progress()
        self.root.after(120, self._process_speech_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    @staticmethod
    def _edge_voice_for(language: str) -> str:
        return EDGE_VOICE_ZH if language == "zh" else EDGE_VOICE_EN

    @staticmethod
    def _edge_cache_path(text: str, voice: str) -> Path:
        key = hashlib.sha256(f"{voice}\0{text}".encode("utf-8")).hexdigest()
        return TTS_CACHE_DIR / f"{key}.mp3"

    @staticmethod
    def _edge_synthesize_to_file(text: str, voice: str, path: Path) -> None:
        async def _run() -> None:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(path))

        asyncio.run(_run())

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="模式：").grid(row=0, column=0, sticky="w")
        self.mode_radio_manual = ttk.Radiobutton(
            frame,
            text="手动",
            variable=self.mode,
            value="manual",
            command=self._on_mode_change,
        )
        self.mode_radio_manual.grid(
            row=0, column=1, sticky="w"
        )
        self.mode_radio_auto = ttk.Radiobutton(
            frame,
            text="自动",
            variable=self.mode,
            value="auto",
            command=self._on_mode_change,
        )
        self.mode_radio_auto.grid(
            row=0, column=2, sticky="w"
        )

        ttk.Label(frame, text="听写方式：").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.dictation_radio_1 = ttk.Radiobutton(
            frame, text="播放英文，听写中文", variable=self.dictation_mode, value="en_to_zh"
        )
        self.dictation_radio_1.grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 0))
        self.dictation_radio_2 = ttk.Radiobutton(
            frame, text="播放中文，听写英文", variable=self.dictation_mode, value="zh_to_en"
        )
        self.dictation_radio_2.grid(row=2, column=1, columnspan=2, sticky="w")

        ttk.Label(frame, text="单元：").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.unit_combo = ttk.Combobox(
            frame, textvariable=self.selected_unit, state="readonly", width=22
        )
        self.unit_combo.grid(row=3, column=1, columnspan=2, sticky="w", pady=(8, 0))
        self.unit_combo.bind("<<ComboboxSelected>>", self._on_unit_change)

        ttk.Label(frame, text="部分：").grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.lesson_combo = ttk.Combobox(
            frame, textvariable=self.selected_lesson, state="readonly", width=22
        )
        self.lesson_combo.grid(row=4, column=1, columnspan=2, sticky="w", pady=(8, 0))
        self.lesson_combo.bind("<<ComboboxSelected>>", self._on_scope_change)

        ttk.Label(frame, text="间隔（秒）：").grid(
            row=5, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(frame, textvariable=self.interval, width=8).grid(
            row=5, column=1, sticky="w", pady=(8, 0)
        )
        ttk.Label(frame, text="默认 10").grid(row=5, column=2, sticky="w", pady=(8, 0))

        self.progress_label = ttk.Label(frame, text="进度：0/0")
        self.progress_label.grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 0))

        self.status_label = ttk.Label(frame, text="就绪", font=("Arial", 13, "bold"))
        self.status_label.grid(row=7, column=0, columnspan=3, sticky="w", pady=(10, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, columnspan=3, sticky="w", pady=16)

        ttk.Button(buttons, text="开始", command=self.start).pack(side="left", padx=4)
        ttk.Button(buttons, text="下一个", command=self.next_word).pack(side="left", padx=4)
        ttk.Button(buttons, text="重播题目", command=self.replay_prompt).pack(
            side="left", padx=4
        )
        ttk.Button(buttons, text="播报提示", command=self.play_hint).pack(
            side="left", padx=4
        )
        self.pause_button = ttk.Button(buttons, text="暂停", command=self.toggle_pause)
        self.pause_button.pack(side="left", padx=4)
        ttk.Button(buttons, text="退出", command=self.exit_dictation).pack(
            side="left", padx=4
        )
        ttk.Button(buttons, text="继续上次", command=self.resume_last).pack(
            side="left", padx=4
        )
        ttk.Button(buttons, text="添加单词", command=self.open_add_words).pack(
            side="left", padx=4
        )

        tips = (
            "语音：Edge 神经语音；每条文案首次需联网合成，之后会缓存在 tts_cache 文件夹。\n"
            "听写方式请在开始前选择。手动：空格或「下一个」。自动：按间隔切换，可用「暂停」。"
        )
        ttk.Label(frame, text=tips, foreground="#555").grid(
            row=9, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(frame, textvariable=self.last_progress_text, foreground="#666").grid(
            row=10, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )

        self.root.bind("<space>", lambda _: self.next_word())

    def _load_words(self):
        try:
            if not WORDS_FILE.is_file():
                raise FileNotFoundError(str(WORDS_FILE))
            with open(WORDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("words.json must be a JSON array.")
            self.words = [w for w in data if isinstance(w, dict) and str(w.get("en", "")).strip()]
            self.index = 0
            self._refresh_filtered_words()
        except Exception as exc:
            messagebox.showerror(
                "加载失败",
                f"请确保 {WORDS_FILE.name} 与 main.py 同目录。\n错误：{exc}",
            )

    def _save_words_to_disk(self) -> None:
        WORDS_FILE.write_text(
            json.dumps(self.words, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _norm_unit(value: str) -> str:
        s = str(value or "").strip()
        if not s:
            return ""
        m = re.match(r"^unit\s*([a-z0-9]+)$", s, re.I)
        if m:
            return f"Unit {m.group(1)}"
        return s

    @staticmethod
    def _norm_lesson(value: str) -> str:
        s = str(value or "").strip()
        if not s:
            return ""
        m = re.match(r"^lesson\s*([a-z0-9]+)$", s, re.I)
        if m:
            return f"Lesson {m.group(1)}"
        return s

    def _refresh_scope_selectors(self) -> None:
        units = sorted(
            {
                self._norm_unit(w.get("unit", ""))
                for w in self.words
                if isinstance(w, dict) and self._norm_unit(w.get("unit", ""))
            }
        )
        if not units:
            units = ["Unit 1"]
        unit_values = ["全部单元"] + units
        self.unit_combo["values"] = unit_values
        if self.selected_unit.get() not in unit_values:
            self.selected_unit.set("全部单元")

        self._refresh_lesson_selector()
        self._update_progress()

    def _refresh_lesson_selector(self) -> None:
        unit = self.selected_unit.get()
        lessons = set()
        for w in self.words:
            if not isinstance(w, dict):
                continue
            w_unit = self._norm_unit(w.get("unit", ""))
            w_lesson = self._norm_lesson(w.get("lesson", ""))
            if not w_lesson:
                continue
            if unit == "全部单元" or unit == w_unit:
                lessons.add(w_lesson)
        lesson_values = ["全部部分"] + sorted(lessons)
        self.lesson_combo["values"] = lesson_values
        if self.selected_lesson.get() not in lesson_values:
            self.selected_lesson.set("全部部分")

    def _on_unit_change(self, _event=None) -> None:
        self._refresh_lesson_selector()
        if not self.session_active:
            self._refresh_filtered_words()

    def _on_scope_change(self, _event=None) -> None:
        if not self.session_active:
            self._refresh_filtered_words()

    def _scope_filtered_words(self) -> list[dict]:
        unit = self.selected_unit.get()
        lesson = self.selected_lesson.get()
        result: list[dict] = []
        for w in self.words:
            if not isinstance(w, dict):
                continue
            w_unit = self._norm_unit(w.get("unit", ""))
            w_lesson = self._norm_lesson(w.get("lesson", ""))
            unit_ok = unit == "全部单元" or unit == w_unit
            lesson_ok = lesson == "全部部分" or lesson == w_lesson
            if unit_ok and lesson_ok:
                result.append(w)
        return result

    def _refresh_filtered_words(self) -> None:
        """Refresh current session scope based on selected unit/lesson."""
        self.filtered_words = self._scope_filtered_words()
        self.index = 0
        self._update_progress()

    def _load_last_progress(self) -> None:
        self.last_progress_text.set(dc.load_last_progress_text())

    def _save_last_progress(self, word: dict | None = None, index: int | None = None) -> None:
        unit = self.selected_unit.get()
        lesson = self.selected_lesson.get()
        status = "exited" if word else "completed"
        dc.save_last_progress(unit, lesson, word=word, index=index, status=status)
        self.last_progress_text.set(dc.load_last_progress_text())

    def _begin_from_index(self, start_index: int) -> None:
        if not self.filtered_words:
            return
        self.index = max(0, min(start_index, len(self.filtered_words) - 1))
        self._set_session_active(True)
        self.show_current()
        self.speak_prompt_twice()
        if self.mode.get() == "auto":
            self.auto_running = True
            self.auto_paused = False
            threading.Thread(target=self._auto_loop, daemon=True).start()
        else:
            self.auto_running = False
            self.auto_paused = False
        self.pause_button.config(text="暂停")
        self._update_mode_controls_state()

    def resume_last(self) -> None:
        record = dc.get_last_progress_record()
        if not record:
            messagebox.showinfo("继续上次", "暂无可继续的听写记录。")
            return

        unit = str(record.get("unit", "")).strip() or "全部单元"
        lesson = str(record.get("lesson", "")).strip() or "全部部分"
        self.selected_unit.set(unit)
        self._refresh_lesson_selector()
        if lesson in self.lesson_combo["values"]:
            self.selected_lesson.set(lesson)
        else:
            self.selected_lesson.set("全部部分")
        self._refresh_filtered_words()
        if not self.filtered_words:
            messagebox.showwarning("继续上次", "原听写范围已无单词，无法继续。")
            return

        word_en = str(record.get("word_en", "")).strip()
        word_zh = str(record.get("word_zh", "")).strip()
        idx = int(record.get("index", 0) or 0)
        resolved = None
        if word_en or word_zh:
            for i, w in enumerate(self.filtered_words):
                en_ok = (not word_en) or (str(w.get("en", "")).strip() == word_en)
                zh_ok = (not word_zh) or dc.word_zh_matches_record(w, word_zh)
                if en_ok and zh_ok:
                    resolved = i
                    break
        if resolved is None and 0 <= idx < len(self.filtered_words):
            resolved = idx
        if resolved is None:
            resolved = 0
            messagebox.showwarning(
                "继续上次",
                "单词位置变化了，没法按照原来的顺序继续听写。\n已从该部分第 1 个单词开始。",
            )

        self._begin_from_index(resolved)

    def _normalize_and_merge(self, raw_list: list[dict]) -> tuple[int, int]:
        """合并导入（与 web 共用 dictation_core：义项合并 + 同形去重）。"""
        du, dl = dc.default_unit_lesson_for_import(
            self.selected_unit.get(), self.selected_lesson.get()
        )
        self.words, added, skipped = dc.normalize_and_merge(
            self.words, raw_list, du, dl
        )
        return added, skipped

    @staticmethod
    def _normalize_word_entry(item: dict) -> dict | None:
        return dc.normalize_word_entry(item)

    def _chinese_speech_text(self, word: dict) -> str:
        """Speak CN：只读中文义项，不读词性（词性仍保存在词库里供以后检索等用）。"""
        zh = str(word.get("zh", "")).strip()
        if not zh:
            return ""
        return zh.replace(";", "，")

    def open_add_words(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("添加 / 导入单词")
        win.geometry("580x460")
        win.transient(self.root)

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        # --- Tab 1: single ---
        tab1 = ttk.Frame(nb, padding=8)
        nb.add(tab1, text="单个添加")

        en_var = tk.StringVar()
        pos_var = tk.StringVar()
        zh_var = tk.StringVar()

        ttk.Label(tab1, text="英文:").grid(row=0, column=0, sticky="nw", pady=4)
        ttk.Entry(tab1, textvariable=en_var, width=40).grid(
            row=0, column=1, sticky="ew", pady=4
        )
        ttk.Label(tab1, text="词性(可选):").grid(row=1, column=0, sticky="nw", pady=4)
        ttk.Entry(tab1, textvariable=pos_var, width=20).grid(
            row=1, column=1, sticky="w", pady=4
        )
        ttk.Label(tab1, text="中文释义:").grid(row=2, column=0, sticky="nw", pady=4)
        ttk.Entry(tab1, textvariable=zh_var, width=40).grid(
            row=2, column=1, sticky="ew", pady=4
        )
        tab1.columnconfigure(1, weight=1)

        hint = (
            "「播报中文」只读中文释义（不读词性）；义项里的分号会读成逗号停顿。\n"
            "批量建议格式：英文,词性,中文（例：convert,v,转变;转换）"
        )
        ttk.Label(tab1, text=hint, foreground="#555", wraplength=520).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=8
        )

        def save_single() -> None:
            en = en_var.get().strip()
            zh = zh_var.get().strip()
            pos = pos_var.get().strip()
            if not en or not zh:
                messagebox.showwarning("提示", "请填写英文和中文。", parent=win)
                return
            item: dict[str, str] = {"en": en, "zh": zh}
            if pos:
                item["pos"] = pos
            added, skipped = self._normalize_and_merge([item])
            if skipped:
                messagebox.showinfo(
                    "提示",
                    "相同的「英文 + 词性」已在词库中，未重复添加。\n"
                    "同一单词不同词性（如 convert v. 与 convert n.）会分别保存。",
                    parent=win,
                )
            elif added:
                self._save_words_to_disk()
                if not self.session_active:
                    self._refresh_filtered_words()
                else:
                    self._update_progress()
                messagebox.showinfo("成功", "已保存到词库（words.json）。", parent=win)
                en_var.set("")
                pos_var.set("")
                zh_var.set("")

        ttk.Button(tab1, text="保存到词库", command=save_single).grid(
            row=4, column=0, columnspan=2, pady=12
        )

        # --- Tab 2: batch lines ---
        tab2 = ttk.Frame(nb, padding=8)
        nb.add(tab2, text="批量粘贴")

        ttk.Label(
            tab2,
            text=(
                "每行一条。推荐（豆包同款）：英文,词性,中文;义项2\n"
                "例：convert,v,转变;转换  与  convert,n,改变信仰者 会同时保留。\n"
                "也支持：在批量中指定位置：unit1lesson1 / lesson2（不带单词行会用于切换当前单元/部分）\n"
                "也支持：英文,词性.中文  或  仅 英文,中文"
            ),
            foreground="#555",
        ).pack(anchor="w")
        batch_txt = scrolledtext.ScrolledText(tab2, height=14, wrap="word", font=("Consolas", 10))
        batch_txt.pack(fill="both", expand=True, pady=6)

        def import_batch() -> None:
            text = batch_txt.get("1.0", "end")
            parsed, bad = parse_batch_text(text)
            items = []
            for p in parsed:
                n = self._normalize_word_entry(p)
                if n:
                    items.append(n)
            if bad and not items:
                messagebox.showerror(
                    "解析失败",
                    "没有解析到有效行。请检查格式。\n示例：abandon,v,放弃;抛弃",
                    parent=win,
                )
                return
            added, skipped = self._normalize_and_merge(items)
            self._save_words_to_disk()
            if not self.session_active:
                self._refresh_filtered_words()
            else:
                self._update_progress()
            msg = f"新增 {added} 条，跳过重复 {skipped} 条。"
            if bad:
                msg += f"\n无法解析行数: {len(bad)}（可检查是否少了逗号）。"
            messagebox.showinfo("批量导入", msg, parent=win)
            if added:
                batch_txt.delete("1.0", "end")

        ttk.Button(tab2, text="解析并加入词库", command=import_batch).pack(pady=6)

        # --- Tab 3: CSV file ---
        tab3 = ttk.Frame(nb, padding=8)
        nb.add(tab3, text="CSV 文件")

        ttk.Label(
            tab3,
            text=(
                "从豆包等导出的 CSV：表头建议包含 英文、中文；若有词性可加一列。\n"
                "也支持无表头三列：英文,词性,中文 或 两列：英文,中文"
            ),
            foreground="#555",
            wraplength=520,
        ).pack(anchor="w")

        def import_csv_path() -> None:
            path = filedialog.askopenfilename(
                parent=win,
                title="选择 CSV 文件",
                filetypes=[
                    ("CSV 表格", "*.csv"),
                    ("文本", "*.txt"),
                    ("所有文件", "*.*"),
                ],
            )
            if not path:
                return
            try:
                raw = Path(path).read_text(encoding="utf-8-sig")
            except Exception as e:
                messagebox.showerror("读取失败", str(e), parent=win)
                return
            items, err_lines = parse_csv_text(raw)
            norms = []
            for it in items:
                n = self._normalize_word_entry(it)
                if n:
                    norms.append(n)
            if not norms:
                messagebox.showerror(
                    "解析失败",
                    "未解析到有效行。请检查编码为 UTF-8，列是否为 英文+中文。",
                    parent=win,
                )
                return
            added, skipped = self._normalize_and_merge(norms)
            self._save_words_to_disk()
            if not self.session_active:
                self._refresh_filtered_words()
            else:
                self._update_progress()
            msg = f"新增 {added} 条，跳过重复 {skipped} 条。"
            if err_lines:
                msg += f"\n问题行约 {len(err_lines)} 行（列不全等）。"
            messagebox.showinfo("CSV 导入", msg, parent=win)

        ttk.Button(tab3, text="选择 CSV 文件并导入", command=import_csv_path).pack(
            pady=16
        )

        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=4)

    def _update_progress(self):
        total = len(self.filtered_words)
        current = min(self.index + 1, total) if total else 0
        self.progress_label.config(text=f"进度：{current}/{total}")

    def _current_word(self):
        if 0 <= self.index < len(self.filtered_words):
            return self.filtered_words[self.index]
        return None

    def _set_session_active(self, active: bool) -> None:
        """Lock dictation mode during an active dictation session."""
        self.session_active = active
        state = "disabled" if active else "normal"
        self.dictation_radio_1.config(state=state)
        self.dictation_radio_2.config(state=state)
        self._update_mode_controls_state()

    def _update_mode_controls_state(self) -> None:
        """Mode controls policy:
        - manual session: mode can switch.
        - auto running (not paused): mode locked.
        - auto paused: mode can switch to manual.
        """
        if not self.session_active:
            state = "normal"
        elif self.mode.get() == "auto" and self.auto_running and not self.auto_paused:
            state = "disabled"
        else:
            state = "normal"
        self.mode_radio_manual.config(state=state)
        self.mode_radio_auto.config(state=state)

    def _on_mode_change(self) -> None:
        if not self.session_active:
            return

        # Automatic dictation running: must pause first before switching manual.
        if self.mode.get() == "manual":
            if self.auto_running and not self.auto_paused:
                self.mode.set("auto")
                messagebox.showinfo("提示", "自动播报中请先点击「暂停」，再切换为手动。")
                self._update_mode_controls_state()
                return
            self.auto_running = False
            self.auto_paused = False
            self.pause_button.config(text="暂停")
            self.status_label.config(text="已切换为手动模式")
            self._update_mode_controls_state()
            return

        # Switch from manual to auto during active session.
        if self.mode.get() == "auto" and not self.auto_running:
            self.auto_running = True
            self.auto_paused = False
            self.pause_button.config(text="暂停")
            self.status_label.config(text="已切换为自动模式")
            threading.Thread(target=self._auto_loop, daemon=True).start()
        self._update_mode_controls_state()

    def _prompt_text_and_language(self, word: dict) -> tuple[str, str]:
        dm = self.dictation_mode.get()
        sense_i = None
        if dm == "zh_to_en":
            sense_i = getattr(self, "_zh_sense_seq", 0)
            self._zh_sense_seq = int(sense_i) + 1
        return dc.prompt_text_and_language(word, dm, sense_i)

    def _hint_text_and_language(self, word: dict) -> tuple[str, str]:
        return dc.hint_text_and_language(word, self.dictation_mode.get())

    def _speak(self, text, language="en"):
        self.speech_queue.put((text, language))

    def _clear_pending_speech(self):
        while True:
            try:
                self.speech_queue.get_nowait()
            except queue.Empty:
                break

    def _stop_current_audio(self):
        self.stop_playback_event.set()

    def _process_speech_queue(self):
        if not self.speech_running:
            try:
                item = self.speech_queue.get_nowait()
            except queue.Empty:
                item = None

            if item:
                self.stop_playback_event.clear()
                self.speech_running = True
                text, language = item

                def work() -> None:
                    try:
                        voice = self._edge_voice_for(language)
                        path = self._edge_cache_path(text, voice)
                        if not path.is_file():
                            path.parent.mkdir(parents=True, exist_ok=True)
                            self._edge_synthesize_to_file(text, voice, path)
                        _play_mp3_blocking_win(path, self.stop_playback_event)
                    except Exception as exc:
                        self.root.after(
                            0,
                            lambda e=str(exc): self.status_label.config(
                                text=f"语音错误（是否需联网？）：{e}"
                            ),
                        )
                    finally:
                        self.root.after(0, lambda: setattr(self, "speech_running", False))

                threading.Thread(target=work, daemon=True).start()

        self.root.after(120, self._process_speech_queue)

    def speak_prompt_twice(self):
        word = self._current_word()
        if not word:
            return
        prompt_text, prompt_lang = self._prompt_text_and_language(word)
        if not prompt_text:
            return

        self._clear_pending_speech()
        self._speak(prompt_text, prompt_lang)
        self._speak(prompt_text, prompt_lang)

    def replay_prompt(self):
        self.speak_prompt_twice()

    def play_hint(self):
        word = self._current_word()
        if not word:
            return
        text, lang = self._hint_text_and_language(word)
        if not text:
            return
        self._speak(text, lang)

    def show_current(self):
        if self.index >= len(self.filtered_words):
            self.status_label.config(text="全部完成。")
            self._save_last_progress()
            self._set_session_active(False)
            return
        self.status_label.config(text=f"第 {self.index + 1} 个单词")
        self._update_progress()

    def start(self):
        self._refresh_filtered_words()
        if not self.filtered_words:
            return

        try:
            if self.interval.get() <= 0:
                raise ValueError
        except Exception:
            messagebox.showwarning("间隔", "间隔必须是正整数。")
            return

        self.index = 0
        self._set_session_active(True)
        self.show_current()
        self.speak_prompt_twice()

        if self.mode.get() == "auto":
            self.auto_running = True
            self.auto_paused = False
            threading.Thread(target=self._auto_loop, daemon=True).start()
        else:
            self.auto_running = False
            self.auto_paused = False
        self.pause_button.config(text="暂停")
        self._update_mode_controls_state()

    def next_word(self):
        if not self.session_active:
            return
        if not self.filtered_words:
            return
        if self.index >= len(self.filtered_words) - 1:
            self.status_label.config(text="全部完成。")
            self._save_last_progress()
            self.auto_running = False
            self.auto_paused = False
            self._set_session_active(False)
            return
        self.index += 1
        self.show_current()
        self.speak_prompt_twice()

    def exit_dictation(self) -> None:
        """退出听写：停止自动切换，并记忆当前题目单词。"""
        if not self.session_active:
            return
        # 停止自动线程下一次调度
        self.auto_running = False
        self.auto_paused = False
        self.pause_button.config(text="暂停")
        self._clear_pending_speech()
        word = self._current_word()
        idx = self.index
        self._set_session_active(False)
        if word:
            self._save_last_progress(word=word, index=idx)
            self.status_label.config(text="已退出（已记忆当前单词）")
        else:
            self.status_label.config(text="已退出。")
            self._save_last_progress(index=idx)

    def _auto_loop(self):
        while self.auto_running and self.index < len(self.filtered_words) - 1:
            wait_seconds = self.interval.get()
            for _ in range(wait_seconds):
                if not self.auto_running:
                    return
                while self.auto_paused and self.auto_running:
                    time.sleep(0.2)
                time.sleep(1)
            if not self.auto_running:
                return
            self.root.after(0, self.next_word)

    def toggle_pause(self):
        if self.mode.get() != "auto":
            messagebox.showinfo("提示", "「暂停」仅在自动模式下可用。")
            return
        if not self.auto_running:
            messagebox.showinfo("提示", "请先在自动模式下点击「开始」。")
            return
        self.auto_paused = not self.auto_paused
        if self.auto_paused:
            # 立即暂停：停止当前音频，不必等两遍播完。
            self._stop_current_audio()
            self._clear_pending_speech()
            self.pause_button.config(text="继续")
        else:
            # 继续后先重播当前题两遍，再进入后续自动流程。
            self.pause_button.config(text="暂停")
            self.speak_prompt_twice()
        self._update_mode_controls_state()

    def test_voice(self):
        self._clear_pending_speech()
        self._speak("Hello, this is English.", "en")
        self._speak("你好，这是中文试听。", "zh")

    def _on_close(self):
        self.auto_running = False
        self.root.destroy()


def main():
    root = tk.Tk()
    DictationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
