/* 英语听写页逻辑（/dictation） */
(function () {
  const $ = (id) => document.getElementById(id);
  const statusEl = $("status");
  const progressEl = $("progress");
  const progressStageEl = $("progressStage");
  const historyList = $("historyList");
  const player = $("player");
  const settingsModal = $("settingsModal");
  const summaryModal = $("summaryModal");
  const libraryModal = $("libraryModal");

  let autoTimer = null;
  let sessionMode = "manual";
  let autoPaused = false;
  let sessionActive = false;

  function pageTitleForLibrary(config) {
    const lid = String((config && config.library_id) || "").trim().toLowerCase();
    if (lid === "primary") return "小学单词";
    if (lid === "junior") return "初中单词";
    if (lid === "senior") return "高中单词";
    if (lid === "cet4") return "英语四级单词";
    if (lid === "cet6") return "英语六级单词";
    return "英语听写";
  }

  function setProgressText(line) {
    progressEl.textContent = line;
    refreshProgressStage();
  }

  function refreshProgressStage() {
    const t = (progressEl && progressEl.textContent) || "";
    const m0 = t.match(/进度：0\/(\d+)/);
    if (m0 && !sessionActive) {
      progressStageEl.textContent = "共 " + m0[1] + " 词 · 点「开始」后听写";
      return;
    }
    const m = t.match(/进度：(\d+)\/(\d+)/);
    if (m) {
      progressStageEl.textContent = "第 " + m[1] + " / " + m[2] + " 个单词";
      return;
    }
    if (t.includes("—") || t.includes("0/0")) {
      progressStageEl.textContent = "选好单元与部分后，点「开始」";
      return;
    }
    progressStageEl.textContent = t.replace(/^进度：/, "") || "—";
  }

  function setDictationModeDisabled(disabled) {
    document.querySelectorAll('input[name="dictation_mode"]').forEach((el) => {
      el.disabled = !!disabled;
    });
  }

  function currentDictationMode() {
    const el = document.querySelector('input[name="dictation_mode"]:checked');
    return el ? el.value : "en_to_zh";
  }

  function clearSpellWordState() {
    $("spellHintBox").classList.add("hidden");
    $("spellHintText").textContent = "";
    $("spellHintLabel").textContent = "";
    $("spellInput").value = "";
  }

  function updateSpellUi() {
    const dm = currentDictationMode();
    const panel = $("spellPanel");
    panel.classList.toggle("hidden", dm !== "en_spell");
    panel.setAttribute("aria-hidden", dm !== "en_spell" ? "true" : "false");
    $("btnHint").textContent = dm === "en_spell" ? "提示" : "播报提示";
    if (dm !== "en_spell") clearSpellWordState();
  }

  document.querySelectorAll('input[name="dictation_mode"]').forEach((el) => {
    el.addEventListener("change", async () => {
      updateSpellUi();
      const v = el.value;
      try {
        await api("/api/settings", { method: "POST", body: { dictation_mode: v } });
      } catch (err) {
        setStatus(err.message);
      }
    });
  });

  function syncModeRadioSelection() {
    const selected = document.querySelector(`input[name="mode"][value="${sessionMode}"]`);
    if (selected) selected.checked = true;
  }

  function updateModeControlsState() {
    const locked = sessionActive && sessionMode === "auto" && !autoPaused;
    document.querySelectorAll('input[name="mode"]').forEach((el) => {
      el.disabled = locked;
    });
  }

  function setStatus(t) {
    statusEl.textContent = t;
  }

  async function api(path, opts = {}) {
    const r = await fetch(path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json", ...opts.headers },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      credentials: "same-origin",
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || r.statusText);
    return data;
  }

  function clearAutoTimer() {
    if (autoTimer) {
      clearTimeout(autoTimer);
      autoTimer = null;
    }
  }

  function playUrls(urls, onDone) {
    if (!urls || !urls.length) {
      if (onDone) onDone();
      return;
    }
    let i = 0;
    function cleanup() {
      player.onerror = null;
      player.onended = null;
    }
    function finish() {
      cleanup();
      if (onDone) onDone();
    }
    function playStep() {
      if (i >= urls.length) {
        finish();
        return;
      }
      const url = urls[i];
      i += 1;
      player.onerror = function () {
        setStatus("无法播放音频：片段加载失败，已跳过。");
        cleanup();
        playStep();
      };
      player.onended = function () {
        cleanup();
        playStep();
      };
      player.src = url;
      try {
        player.load();
      } catch (_) {}
      const p = player.play();
      if (p && typeof p.then === "function") {
        p.catch(function (e) {
          setStatus("无法播放音频：" + (e && e.message ? e.message : "播放失败"));
          cleanup();
          playStep();
        });
      }
    }
    playStep();
  }

  function syncBodyModalOpen() {
    const sOpen = summaryModal && !summaryModal.classList.contains("hidden");
    const gOpen = settingsModal && !settingsModal.classList.contains("hidden");
    const lOpen = libraryModal && !libraryModal.classList.contains("hidden");
    document.body.classList.toggle("modal-open", !!(sOpen || gOpen || lOpen));
  }

  function openSummaryModal(summary) {
    const st = summary.stats || {};
    const answered = st.answered || 0;
    const correct = st.correct || 0;
    const wrongRows = summary.wrong_rows || [];
    const unsubmittedRows = summary.unsubmitted_rows || [];
    const hintRows = summary.hint_rows || [];
    const unanswer = st.unanswered != null ? st.unanswered : unsubmittedRows.length;
    $("summaryIntro").textContent =
      answered === 0
        ? "本次没有点击「确认」的题目，未计入正确率。"
        : (function () {
            const pct = Math.round((100 * correct) / answered);
            const head = "本次已确认 " + answered + " 题，正确率 " + pct + "%，";
            if (pct >= 80) return head + "很棒！";
            if (pct >= 50) return head + "继续加油！";
            return head + "多练会更好！";
          })();
    $("summaryNote").textContent =
      (unanswer > 0 ? "有 " + unanswer + " 题未点「确认」，已列入下方「未点击确认」表。" : "") +
      "未点击确认的词未计入正确率。拼写错误的题已写入错题本，之后可在「错题回顾」中查看（功能入口预留中）。";

    const wWrap = $("summaryWrongWrap");
    const wBody = $("summaryWrongTbody");
    wBody.innerHTML = "";
    if (wrongRows.length === 0) {
      wWrap.classList.add("hidden");
    } else {
      wWrap.classList.remove("hidden");
      wrongRows.forEach(function (r) {
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" +
          escapeHtml(r.en || "") +
          "</td><td>" +
          escapeHtml(r.zh || "") +
          "</td><td>" +
          escapeHtml(r.attempt || "") +
          "</td>";
        wBody.appendChild(tr);
      });
    }

    const uWrap = $("summaryUnsubmittedWrap");
    const uBody = $("summaryUnsubmittedTbody");
    uBody.innerHTML = "";
    if (unsubmittedRows.length === 0) {
      uWrap.classList.add("hidden");
    } else {
      uWrap.classList.remove("hidden");
      unsubmittedRows.forEach(function (r) {
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" +
          escapeHtml(r.en || "") +
          "</td><td>" +
          escapeHtml(r.zh || "") +
          "</td><td>未点击确认</td>";
        uBody.appendChild(tr);
      });
    }

    const hWrap = $("summaryHintWrap");
    const hBody = $("summaryHintTbody");
    hBody.innerHTML = "";
    if (hintRows.length === 0) {
      hWrap.classList.add("hidden");
    } else {
      hWrap.classList.remove("hidden");
      hintRows.forEach(function (r) {
        const tr = document.createElement("tr");
        tr.innerHTML =
          "<td>" +
          escapeHtml(r.en || "") +
          "</td><td>" +
          escapeHtml(r.zh || "") +
          "</td><td>" +
          escapeHtml(String(r.hint_count != null ? r.hint_count : "")) +
          "</td>";
        hBody.appendChild(tr);
      });
    }

    summaryModal.classList.remove("hidden");
    summaryModal.setAttribute("aria-hidden", "false");
    syncBodyModalOpen();
    $("btnSummaryClose").focus();
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function closeSummaryModal() {
    summaryModal.classList.add("hidden");
    summaryModal.setAttribute("aria-hidden", "true");
    syncBodyModalOpen();
  }

  async function handleNextResponse(d) {
    setStatus(d.status || "");
    if (!d.done && d.total != null && d.index != null) {
      setProgressText("进度：" + (d.index + 1) + "/" + d.total);
    }
    if (d.done) {
      sessionActive = false;
      setDictationModeDisabled(false);
      updateModeControlsState();
      clearSpellWordState();
      if (d.spell_summary) openSummaryModal(d.spell_summary);
      await loadConfig();
      return true;
    }
    clearSpellWordState();
    return false;
  }

  async function postNextAndHandle() {
    clearAutoTimer();
    if (!sessionActive) {
      setStatus("已退出，点击「开始」继续。");
      return;
    }
    try {
      const d = await api("/api/next", { method: "POST" });
      const finished = await handleNextResponse(d);
      if (finished) return;
      playUrls(d.audio_urls, function () {
        if (sessionMode === "auto" && !autoPaused) scheduleAutoNext();
        if (currentDictationMode() === "en_spell") $("spellInput").focus();
      });
    } catch (e) {
      setStatus(e.message);
    }
  }

  function scheduleAutoNext() {
    clearAutoTimer();
    if (!sessionActive) return;
    if (sessionMode !== "auto" || autoPaused) return;
    const sec = parseInt($("interval").value, 10) || 10;
    autoTimer = setTimeout(async () => {
      autoTimer = null;
      if (!sessionActive) return;
      if (autoPaused || sessionMode !== "auto") return;
      try {
        const d = await api("/api/next", { method: "POST" });
        const finished = await handleNextResponse(d);
        if (finished) return;
        playUrls(d.audio_urls, function () {
          if (sessionMode === "auto" && !autoPaused) scheduleAutoNext();
          if (currentDictationMode() === "en_spell") $("spellInput").focus();
        });
      } catch (e) {
        setStatus(e.message);
      }
    }, sec * 1000);
  }

  async function loadConfig() {
    const c = await api("/api/config");
    $("lastProgress").textContent = c.last_progress || "";
    $("currentLibraryLabel").textContent = c.current_library_label || "当前词库（words.json）";
    const title = pageTitleForLibrary(c);
    if ($("dictationTitle")) $("dictationTitle").textContent = title;
    document.title = title + " — 英语单词听写";
    const unitSel = $("unit");
    const lessonSel = $("lesson");
    unitSel.innerHTML = "";
    c.units.forEach((u) => {
      const o = document.createElement("option");
      o.value = u;
      o.textContent = u;
      if (u === c.unit) o.selected = true;
      unitSel.appendChild(o);
    });
    lessonSel.innerHTML = "";
    c.lessons.forEach((l) => {
      const o = document.createElement("option");
      o.value = l;
      o.textContent = l;
      if (l === c.lesson) o.selected = true;
      lessonSel.appendChild(o);
    });
    const modeEl = document.querySelector(`input[name="mode"][value="${c.mode}"]`);
    if (modeEl) modeEl.checked = true;
    if (c.mode === "manual" || c.mode === "auto") {
      sessionMode = c.mode;
    }
    const dmEl = document.querySelector(`input[name="dictation_mode"][value="${c.dictation_mode}"]`);
    if (dmEl) dmEl.checked = true;
    updateSpellUi();
    $("interval").value = c.interval;
    // 听写进行中勿把「进度：1/n」覆盖成「进度：0/n」（否则第一题大字与状态行不一致）
    if (!sessionActive) {
      setProgressText(c.word_count ? "进度：0/" + c.word_count : "进度：0/0");
    }
    renderHistory(c.history || []);
  }

  function historyText(it) {
    const ts = it.ts || "—";
    const unit = it.unit || "全部单元";
    const lesson = it.lesson || "全部部分";
    const en = it.word_en || "";
    const zh = it.word_zh || "";
    const st = it.status === "completed" ? "完成" : "退出";
    return `${ts}｜${unit} / ${lesson}｜${st}${en || zh ? `｜${en}${zh ? " / " + zh : ""}` : ""}`;
  }

  async function resumeWithPayload(d) {
    sessionActive = true;
    const s = readSettings();
    sessionMode = s.mode;
    $("btnPause").textContent = "暂停";
    $("unit").value = d.unit;
    $("lesson").value = d.lesson;
    setDictationModeDisabled(false);
    updateModeControlsState();
    clearSpellWordState();
    setStatus(d.status || "已恢复听写");
    if (d.warning) alert(d.warning);
    setProgressText("进度：" + (d.index + 1) + "/" + d.total);
    playUrls(d.audio_urls, () => {
      if (sessionMode === "auto" && !autoPaused) scheduleAutoNext();
      if (currentDictationMode() === "en_spell") $("spellInput").focus();
    });
  }

  function renderHistory(items) {
    historyList.innerHTML = "";
    if (!items.length) {
      const li = document.createElement("li");
      li.textContent = "暂无历史记录。";
      historyList.appendChild(li);
      return;
    }
    items.forEach((it, idx) => {
      const li = document.createElement("li");
      li.className = "history-list-item";
      const row = document.createElement("div");
      row.className = "history-item-row";
      const b = document.createElement("button");
      b.type = "button";
      b.className = "history-item-btn";
      b.textContent = historyText(it);
      b.addEventListener("click", async () => {
        clearAutoTimer();
        autoPaused = false;
        try {
          const d = await api("/api/resume/history", {
            method: "POST",
            body: { history_index: idx },
          });
          await resumeWithPayload(d);
          await loadConfig();
        } catch (e) {
          setStatus(e.message);
        }
      });
      const del = document.createElement("button");
      del.type = "button";
      del.className = "history-item-delete";
      del.setAttribute("aria-label", "删除此条历史");
      del.title = "删除";
      del.textContent = "×";
      del.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        try {
          await api("/api/progress/history/delete", {
            method: "POST",
            body: { history_index: idx },
          });
          await loadConfig();
        } catch (e) {
          setStatus(e.message);
        }
      });
      row.appendChild(b);
      row.appendChild(del);
      li.appendChild(row);
      historyList.appendChild(li);
    });
  }

  function readSettings() {
    return {
      unit: $("unit").value,
      lesson: $("lesson").value,
      mode: document.querySelector('input[name="mode"]:checked').value,
      dictation_mode: document.querySelector('input[name="dictation_mode"]:checked').value,
      interval: parseInt($("interval").value, 10) || 10,
    };
  }

  $("unit").addEventListener("change", async () => {
    await api("/api/settings", { method: "POST", body: { unit: $("unit").value } });
    const c = await api("/api/config");
    const lessonSel = $("lesson");
    lessonSel.innerHTML = "";
    c.lessons.forEach((l) => {
      const o = document.createElement("option");
      o.value = l;
      o.textContent = l;
      if (l === c.lesson) o.selected = true;
      lessonSel.appendChild(o);
    });
    setProgressText("进度：0/" + c.word_count);
  });

  $("lesson").addEventListener("change", async () => {
    await api("/api/settings", {
      method: "POST",
      body: { unit: $("unit").value, lesson: $("lesson").value },
    });
    const c = await api("/api/config");
    setProgressText("进度：0/" + c.word_count);
  });

  $("btnStart").addEventListener("click", async () => {
    clearAutoTimer();
    const s = readSettings();
    sessionMode = s.mode;
    autoPaused = false;
    sessionActive = false;
    $("btnPause").textContent = "暂停";
    syncModeRadioSelection();
    updateModeControlsState();
    try {
      const d = await api("/api/start", { method: "POST", body: s });
      sessionActive = true;
      setDictationModeDisabled(true);
      updateModeControlsState();
      clearSpellWordState();
      setStatus(d.status);
      setProgressText("进度：1/" + d.total);
      playUrls(d.audio_urls, () => {
        if (sessionMode === "auto") scheduleAutoNext();
        if (s.dictation_mode === "en_spell") $("spellInput").focus();
      });
    } catch (e) {
      setStatus(e.message);
    }
    await loadConfig();
  });

  $("btnNext").addEventListener("click", function () {
    postNextAndHandle();
  });

  $("btnReplay").addEventListener("click", async () => {
    try {
      const d = await api("/api/replay", { method: "POST" });
      setStatus(d.status);
      playUrls(d.audio_urls);
    } catch (e) {
      setStatus(e.message);
    }
  });

  $("btnHint").addEventListener("click", async () => {
    try {
      const dm = currentDictationMode();
      const d = await api("/api/hint", { method: "POST" });
      if (dm === "en_spell" && d.hint_text != null && d.hint_text !== "") {
        const kind = d.hint_kind || "";
        $("spellHintLabel").textContent = kind === "answer" ? "原文" : "巧记";
        $("spellHintText").textContent = d.hint_text;
        $("spellHintBox").classList.remove("hidden");
        return;
      }
      playUrls(d.audio_urls);
    } catch (e) {
      setStatus(e.message);
    }
  });

  $("btnPause").addEventListener("click", async () => {
    try {
      const d = await api("/api/pause", { method: "POST" });
      autoPaused = d.paused;
      $("btnPause").textContent = d.paused ? "继续" : "暂停";
      if (d.paused) {
        clearAutoTimer();
        player.pause();
      } else if (sessionMode === "auto") {
        const rp = await api("/api/replay", { method: "POST" });
        playUrls(rp.audio_urls, () => {
          if (sessionMode === "auto" && !autoPaused) scheduleAutoNext();
        });
      }
      updateModeControlsState();
    } catch (e) {
      setStatus(e.message);
    }
  });

  $("btnExit").addEventListener("click", async () => {
    if (!sessionActive) {
      setStatus("未在听写中。");
      return;
    }
    clearAutoTimer();
    autoPaused = true;
    sessionActive = false;
    player.pause();
    setDictationModeDisabled(false);
    updateModeControlsState();
    clearSpellWordState();
    try {
      const d = await api("/api/exit", { method: "POST" });
      setStatus(d.status || "已退出。");
      if (d.total && d.index != null) {
        setProgressText("进度：" + (d.index + 1) + "/" + d.total);
      }
      if (d.last_progress) $("lastProgress").textContent = d.last_progress;
      if (d.spell_summary) openSummaryModal(d.spell_summary);
      await loadConfig();
    } catch (e) {
      setStatus(e.message);
    }
  });

  $("btnResumeLast").addEventListener("click", async () => {
    clearAutoTimer();
    autoPaused = false;
    $("btnPause").textContent = "暂停";
    try {
      const d = await api("/api/resume", { method: "POST" });
      await resumeWithPayload(d);
    } catch (e) {
      setStatus(e.message);
    }
    await loadConfig();
  });

  function toggleHistoryPanel() {
    const panel = $("historyPanel");
    const show = panel.classList.contains("hidden");
    panel.classList.toggle("hidden", !show);
    $("btnHistoryToggle").setAttribute("aria-expanded", show ? "true" : "false");
  }
  $("btnHistoryToggle").addEventListener("click", toggleHistoryPanel);
  $("btnHistoryToggle").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggleHistoryPanel();
    }
  });

  document.querySelectorAll('input[name="mode"]').forEach((radio) => {
    radio.addEventListener("change", async () => {
      const picked = document.querySelector('input[name="mode"]:checked').value;
      if (!sessionActive) {
        sessionMode = picked;
        autoPaused = false;
        updateModeControlsState();
        return;
      }

      if (sessionMode === "auto" && !autoPaused && picked === "manual") {
        setStatus("自动播报中，请先点「暂停」再切换手动。");
        syncModeRadioSelection();
        updateModeControlsState();
        return;
      }

      try {
        await api("/api/settings", { method: "POST", body: { mode: picked } });
      } catch (e) {
        setStatus(e.message);
        syncModeRadioSelection();
        updateModeControlsState();
        return;
      }

      if (picked === "auto" && sessionMode !== "auto") {
        sessionMode = "auto";
        autoPaused = false;
        $("btnPause").textContent = "暂停";
        setStatus("已切换为自动模式。");
        scheduleAutoNext();
      } else if (picked === "manual") {
        sessionMode = "manual";
        autoPaused = false;
        clearAutoTimer();
        $("btnPause").textContent = "暂停";
        setStatus("已切换为手动模式。");
      }
      syncModeRadioSelection();
      updateModeControlsState();
    });
  });

  $("btnGotoImport").addEventListener("click", () => {
    window.location.href = "/import";
  });

  function closeLibraryModal() {
    if (!libraryModal) return;
    libraryModal.classList.add("hidden");
    libraryModal.setAttribute("aria-hidden", "true");
    syncBodyModalOpen();
  }

  function openLibraryModal(c) {
    if (!libraryModal || !$("libraryList")) return;
    const list = $("libraryList");
    list.innerHTML = "";
    const cur = c.library_id || "current";
    (c.libraries || []).forEach((lib) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "library-picker-row" + (lib.id === cur ? " is-current" : "");
      const extra = lib.exists ? lib.word_count + " 词" : "暂无文件（0 词）";
      row.textContent = (lib.label || lib.id) + " · " + extra;
      row.addEventListener("click", async () => {
        if (lib.id === cur) {
          setStatus("已是当前词库。");
          closeLibraryModal();
          return;
        }
        try {
          await api("/api/library/select", { method: "POST", body: { library_id: lib.id } });
          closeLibraryModal();
          await loadConfig();
          setStatus("已切换为「" + (lib.label || lib.id) + "」。");
        } catch (e) {
          setStatus(e.message);
        }
      });
      list.appendChild(row);
    });
    libraryModal.classList.remove("hidden");
    libraryModal.setAttribute("aria-hidden", "false");
    syncBodyModalOpen();
  }

  $("btnSwitchLibrary").addEventListener("click", async () => {
    try {
      const c = await api("/api/config");
      openLibraryModal(c);
    } catch (e) {
      setStatus(e.message);
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space") return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
    if (!sessionActive) {
      e.preventDefault();
      setStatus("已退出，点击「开始」继续。");
      return;
    }
    e.preventDefault();
    $("btnNext").click();
  });

  /* 设置弹层 */
  function openSettingsModal() {
    settingsModal.classList.remove("hidden");
    settingsModal.setAttribute("aria-hidden", "false");
    syncBodyModalOpen();
  }
  function closeSettingsModal() {
    settingsModal.classList.add("hidden");
    settingsModal.setAttribute("aria-hidden", "true");
    syncBodyModalOpen();
  }
  $("btnSettingsOpen").addEventListener("click", openSettingsModal);
  $("btnSettingsClose").addEventListener("click", closeSettingsModal);
  $("settingsModalBackdrop").addEventListener("click", closeSettingsModal);
  if ($("libraryModalBackdrop")) $("libraryModalBackdrop").addEventListener("click", closeLibraryModal);
  if ($("btnLibraryClose")) $("btnLibraryClose").addEventListener("click", closeLibraryModal);
  $("summaryModalBackdrop").addEventListener("click", closeSummaryModal);
  $("btnSummaryClose").addEventListener("click", closeSummaryModal);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (summaryModal && !summaryModal.classList.contains("hidden")) {
      closeSummaryModal();
      return;
    }
    if (libraryModal && !libraryModal.classList.contains("hidden")) {
      closeLibraryModal();
      return;
    }
    if (settingsModal && !settingsModal.classList.contains("hidden")) {
      closeSettingsModal();
    }
  });

  async function spellSubmitFromUi() {
    if (!sessionActive || currentDictationMode() !== "en_spell") return;
    const raw = $("spellInput").value;
    if (!String(raw).trim()) {
      setStatus("请先输入拼写再确认。");
      return;
    }
    try {
      await api("/api/spell/submit", { method: "POST", body: { text: raw } });
    } catch (err) {
      const m = err && err.message ? String(err.message) : "";
      if (/not\s*found/i.test(m) || m === "404") {
        setStatus("无法提交拼写：服务端可能未重启，请关闭后重新运行 python web_app.py 再试。");
      } else {
        setStatus(m || "提交失败");
      }
      return;
    }
    clearAutoTimer();
    await postNextAndHandle();
  }

  $("btnSpellConfirm").addEventListener("click", function () {
    spellSubmitFromUi();
  });
  $("spellInput").addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    spellSubmitFromUi();
  });

  const PREF_DM_KEY = "dictation.pref.dictationMode";
  async function applyPreferredSettings() {
    const preferredDm = localStorage.getItem(PREF_DM_KEY);
    if (
      preferredDm &&
      (preferredDm === "en_to_zh" || preferredDm === "zh_to_en" || preferredDm === "en_spell")
    ) {
      const dmEl = document.querySelector(`input[name="dictation_mode"][value="${preferredDm}"]`);
      if (dmEl) dmEl.checked = true;
      await api("/api/settings", { method: "POST", body: { dictation_mode: preferredDm } }).catch(() => {});
    }
  }

  loadConfig()
    .then(() => applyPreferredSettings())
    .catch((e) => {
      const detail =
        e && e.message === "Failed to fetch"
          ? "无法连接本机服务，请确认已在终端运行 python web_app.py，且地址栏的主机与端口与该进程一致。"
          : e.message;
      setStatus("加载失败：" + detail);
    });
  setDictationModeDisabled(false);
  updateModeControlsState();
  refreshProgressStage();
})();
