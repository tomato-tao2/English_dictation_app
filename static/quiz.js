(() => {
  const $ = (id) => document.getElementById(id);
  const els = {
    status: $("quizStatus"),
    prompt: $("quizPrompt"),
    hintLine: $("quizHintLine"),
    options: $("quizOptions"),
    explain: $("quizExplain"),
    finish: $("quizFinishSummary"),
    btnStart: $("btnQuizStart"),
    btnExit: $("btnQuizExit"),
    btnSettings: $("btnQuizSettings"),
    btnKnown: $("btnKnown"),
    btnUnknown: $("btnUnknown"),
    recallActions: $("quizRecallActions"),
    modal: $("quizSettingsModal"),
    wrongModal: $("quizWrongModal"),
    wrongText: $("quizWrongText"),
    btnWrongNext: $("btnQuizWrongNext"),
    form: $("quizSettingsForm"),
    mode: $("quizMode"),
    unit: $("quizUnit"),
    lesson: $("quizLesson"),
    btnCancel: $("btnQuizSettingsCancel"),
  };

  const state = { active: false, current: null };

  async function api(path, opts = {}) {
    const r = await fetch(path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json" },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      credentials: "same-origin",
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText || "请求失败");
    return data;
  }

  function setStatus(msg) {
    els.status.textContent = msg || "";
  }

  async function showWrongModal(text) {
    if (!els.wrongModal || !els.wrongText || !els.btnWrongNext) return;
    els.wrongText.textContent = text || "请看正确答案。";
    els.wrongModal.showModal();
    await new Promise((resolve) => {
      const onNext = () => {
        els.btnWrongNext.removeEventListener("click", onNext);
        resolve();
      };
      els.btnWrongNext.addEventListener("click", onNext);
    });
    els.wrongModal.close();
  }

  function renderFinishSummary(data) {
    const rows = [...(data.wrong_rows || []), ...(data.unknown_rows || [])];
    if (!rows.length) {
      els.finish.classList.add("hidden");
      els.finish.innerHTML = "";
      return;
    }
    const body = rows
      .map(
        (r) =>
          `<tr><td>${r.en || "—"}</td><td>${r.zh || "—"}</td><td>${r.attempt || "—"}</td></tr>`
      )
      .join("");
    els.finish.classList.remove("hidden");
    els.finish.innerHTML = `
      <p class="summary-section-title">本轮不会 / 做错（建议马上复习）</p>
      <div class="summary-table-scroll">
        <table class="summary-table">
          <thead><tr><th>英文</th><th>中文</th><th>你的作答</th></tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>`;
  }

  function renderQuestion(data) {
    if (data.done) {
      state.current = null;
      state.active = false;
      els.options.innerHTML = "";
      els.recallActions.classList.add("hidden");
      els.prompt.textContent = "本轮完成";
      els.hintLine.textContent = "";
      els.explain.textContent = "";
      setStatus(data.status || "本轮完成");
      renderFinishSummary(data);
      return;
    }
    state.current = data;
    els.finish.classList.add("hidden");
    els.finish.innerHTML = "";
    els.prompt.textContent = data.prompt || "";
    els.hintLine.textContent =
      data.kind === "recall" ? "先自己回忆中文，再点会 / 不会" : "请选择正确中文";
    els.explain.textContent = "";
    setStatus(`第 ${data.index} / ${data.total} 题`);
    if (data.kind === "recall") {
      els.options.innerHTML = "";
      els.recallActions.classList.remove("hidden");
      return;
    }
    els.recallActions.classList.add("hidden");
    els.options.innerHTML = "";
    (data.options || []).forEach((opt) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "quiz-option-btn";
      btn.textContent = opt.text || "—";
      btn.onclick = () => submitChoice(opt.id, btn);
      els.options.appendChild(btn);
    });
  }

  async function loadNext() {
    const data = await api("/api/quiz/next", { method: "POST" });
    renderQuestion(data);
  }

  async function submitRecall(known) {
    if (!state.active || !state.current) return;
    try {
      const ret = await api("/api/quiz/submit", {
        method: "POST",
        body: { known: known ? "yes" : "no" },
      });
      if (!ret.correct) {
        await showWrongModal(ret.explanation || "请看正确答案。");
        await loadNext();
        return;
      }
      setTimeout(loadNext, known ? 200 : 260);
    } catch (e) {
      setStatus(e.message || "提交失败");
    }
  }

  async function submitChoice(optionId, clickedBtn) {
    if (!state.active || !state.current) return;
    const buttons = els.options.querySelectorAll("button");
    buttons.forEach((b) => (b.disabled = true));
    try {
      const ret = await api("/api/quiz/submit", {
        method: "POST",
        body: { option_id: optionId },
      });
      if (ret.correct) {
        setStatus("正确");
        setTimeout(loadNext, 220);
        return;
      }
      setStatus("错误");
      clickedBtn.classList.add("quiz-option-btn--wrong");
      buttons.forEach((b, idx) => {
        const oid = state.current?.options?.[idx]?.id;
        if (oid === ret.correct_id) b.classList.add("quiz-option-btn--correct");
      });
      await showWrongModal(ret.explanation || "请看正确答案。");
      await loadNext();
    } catch (e) {
      setStatus(e.message || "提交失败");
      buttons.forEach((b) => (b.disabled = false));
    }
  }

  async function loadConfig() {
    const data = await api("/api/quiz/config");
    els.mode.value = data.quiz_mode || "en_pick_zh";
    els.unit.innerHTML = (data.units || [])
      .map((u) => `<option value="${u}">${u}</option>`)
      .join("");
    els.unit.value = data.unit || "全部单元";
    els.lesson.innerHTML = (data.lessons || [])
      .map((l) => `<option value="${l}">${l}</option>`)
      .join("");
    els.lesson.value = data.lesson || "全部部分";
    setStatus(`当前可抽背 ${data.word_count || 0} 词`);
  }

  els.btnStart.addEventListener("click", async () => {
    try {
      const data = await api("/api/quiz/start", {
        method: "POST",
        body: {
          quiz_mode: els.mode.value,
          unit: els.unit.value,
          lesson: els.lesson.value,
        },
      });
      state.active = true;
      renderQuestion(data);
    } catch (e) {
      setStatus(e.message || "开始失败");
    }
  });

  els.btnExit.addEventListener("click", async () => {
    try {
      const ret = await api("/api/quiz/exit", { method: "POST" });
      state.active = false;
      setStatus(`已退出，保存 ${ret.saved_wrong || 0} 条抽背错题`);
      els.prompt.textContent = "已退出";
      els.hintLine.textContent = "";
      els.options.innerHTML = "";
      els.recallActions.classList.add("hidden");
      els.explain.textContent = "";
    } catch (e) {
      setStatus(e.message || "退出失败");
    }
  });

  els.btnKnown.addEventListener("click", () => submitRecall(true));
  els.btnUnknown.addEventListener("click", () => submitRecall(false));
  els.btnSettings.addEventListener("click", () => els.modal.showModal());
  els.btnCancel.addEventListener("click", () => els.modal.close());

  els.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const ret = await api("/api/quiz/settings", {
        method: "POST",
        body: {
          quiz_mode: els.mode.value,
          unit: els.unit.value,
          lesson: els.lesson.value,
        },
      });
      const oldLesson = els.lesson.value;
      els.lesson.innerHTML = (ret.lessons || [])
        .map((l) => `<option value="${l}">${l}</option>`)
        .join("");
      if ([...els.lesson.options].some((o) => o.value === oldLesson)) {
        els.lesson.value = oldLesson;
      }
      setStatus(`设置已保存，可抽背 ${ret.word_count || 0} 词`);
      els.modal.close();
    } catch (err) {
      setStatus(err.message || "保存设置失败");
    }
  });

  els.unit.addEventListener("change", async () => {
    try {
      const ret = await api("/api/quiz/settings", {
        method: "POST",
        body: { unit: els.unit.value, quiz_mode: els.mode.value },
      });
      els.lesson.innerHTML = (ret.lessons || [])
        .map((l) => `<option value="${l}">${l}</option>`)
        .join("");
      setStatus(`当前可抽背 ${ret.word_count || 0} 词`);
    } catch (e) {
      setStatus(e.message || "更新失败");
    }
  });

  loadConfig().catch((e) => setStatus(e.message || "配置加载失败"));
})();
