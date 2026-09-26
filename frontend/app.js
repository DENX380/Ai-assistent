// ---------------------------------------------------------------------
// Локальный ИИ-агент — фронтенд. Ванильный JS, без сборки и фреймворков.
// ---------------------------------------------------------------------

const messagesEl = document.getElementById("messages");
const toolActivityEl = document.getElementById("tool-activity");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const modelSelect = document.getElementById("model-select");

const canvasViewport = document.getElementById("canvas-viewport");
const canvasEl = document.getElementById("canvas");
const canvasHint = document.getElementById("canvas-hint");
const widgetCountEl = document.getElementById("widget-count");
const boardsBarEl = document.getElementById("boards-bar");
const searchInput = document.getElementById("widget-search");

const zoomInBtn = document.getElementById("zoom-in-btn");
const zoomOutBtn = document.getElementById("zoom-out-btn");
const zoomResetBtn = document.getElementById("zoom-reset-btn");
const zoomLevelEl = document.getElementById("zoom-level");

const confirmOverlay = document.getElementById("confirm-overlay");
const confirmCommandEl = document.getElementById("confirm-command");
const confirmAllowBtn = document.getElementById("confirm-allow");
const confirmDenyBtn = document.getElementById("confirm-deny");

const promptBtn = document.getElementById("prompt-btn");
const promptOverlay = document.getElementById("prompt-overlay");
const promptClose = document.getElementById("prompt-close");
const promptContent = document.getElementById("prompt-content");

const settingsBtn = document.getElementById("settings-btn");
const settingsOverlay = document.getElementById("settings-overlay");
const settingsClose = document.getElementById("settings-close");
const accentSwatchesEl = document.getElementById("accent-swatches");
const bgOptionsEl = document.getElementById("bg-options");
const radiusSlider = document.getElementById("radius-slider");
const radiusValueEl = document.getElementById("radius-value");
const settingsResetBtn = document.getElementById("settings-reset");

let currentModel = "";
let pendingConfirmId = null;
let widgetsById = new Map(); // id -> {data, el}
let isSending = false;
let currentBoardId = localStorage.getItem("lastBoardId") || "default";

if (location.protocol === "file:") {
  alert(
    "Страница открыта как локальный файл (file://) — сохранение и работа с " +
    "агентом не будут работать. Запустите сервер командой `python run.py` и " +
    "откройте http://127.0.0.1:8000 в браузере."
  );
}

// ---------------------- Настройки/персонализация ----------------------

const ACCENT_PRESETS = ["#6c8cff", "#7dff9e", "#ff8a6c", "#f5b942", "#c86cff", "#ff6ba0", "#4fd1e0"];
const DEFAULT_SETTINGS = { accent: "#6c8cff", background: "dots", radius: 14 };

function loadSettings() {
  try {
    return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem("uiSettings") || "{}") };
  } catch (e) {
    return { ...DEFAULT_SETTINGS };
  }
}

function saveSettings(settings) {
  localStorage.setItem("uiSettings", JSON.stringify(settings));
}

function shadeColor(hex, percent) {
  // затемняет hex-цвет на percent (0..1) — используется для --accent-dim
  const num = parseInt(hex.replace("#", ""), 16);
  const r = Math.max(0, Math.floor((num >> 16) * (1 - percent)));
  const g = Math.max(0, Math.floor(((num >> 8) & 0xff) * (1 - percent)));
  const b = Math.max(0, Math.floor((num & 0xff) * (1 - percent)));
  return `rgb(${r}, ${g}, ${b})`;
}

function applySettings(settings) {
  const root = document.documentElement;
  root.style.setProperty("--accent", settings.accent);
  root.style.setProperty("--accent-dim", shadeColor(settings.accent, 0.45));
  root.style.setProperty("--radius", settings.radius + "px");

  canvasViewport.classList.remove("bg-dots", "bg-grid", "bg-plain");
  canvasViewport.classList.add("bg-" + settings.background);

  radiusSlider.value = settings.radius;
  radiusValueEl.textContent = settings.radius + "px";

  for (const btn of accentSwatchesEl.querySelectorAll(".swatch")) {
    btn.classList.toggle("selected", btn.dataset.color.toLowerCase() === settings.accent.toLowerCase());
  }
  for (const btn of bgOptionsEl.querySelectorAll(".option-btn")) {
    btn.classList.toggle("selected", btn.dataset.bg === settings.background);
  }
}

function initSettingsUI() {
  const settings = loadSettings();

  for (const color of ACCENT_PRESETS) {
    const btn = document.createElement("button");
    btn.className = "swatch";
    btn.style.background = color;
    btn.dataset.color = color;
    btn.title = color;
    btn.addEventListener("click", () => {
      const s = loadSettings();
      s.accent = color;
      saveSettings(s);
      applySettings(s);
    });
    accentSwatchesEl.appendChild(btn);
  }

  for (const btn of bgOptionsEl.querySelectorAll(".option-btn")) {
    btn.addEventListener("click", () => {
      const s = loadSettings();
      s.background = btn.dataset.bg;
      saveSettings(s);
      applySettings(s);
    });
  }

  radiusSlider.addEventListener("input", () => {
    const s = loadSettings();
    s.radius = parseInt(radiusSlider.value, 10);
    saveSettings(s);
    applySettings(s);
  });

  settingsResetBtn.addEventListener("click", () => {
    saveSettings(DEFAULT_SETTINGS);
    applySettings(DEFAULT_SETTINGS);
  });

  applySettings(settings);
}

settingsBtn.addEventListener("click", () => settingsOverlay.classList.remove("hidden"));
settingsClose.addEventListener("click", () => settingsOverlay.classList.add("hidden"));
settingsOverlay.addEventListener("click", (e) => {
  if (e.target === settingsOverlay) settingsOverlay.classList.add("hidden");
});

// ---------------------- Промпт агента (быстрый просмотр) ----------------------

promptBtn.addEventListener("click", async () => {
  promptOverlay.classList.remove("hidden");
  promptContent.textContent = "Загрузка…";
  try {
    const res = await fetch(`/api/system-prompt?board_id=${encodeURIComponent(currentBoardId)}`);
    const data = await res.json();
    promptContent.textContent = data.prompt || "(пусто)";
  } catch (e) {
    promptContent.textContent = "Не удалось загрузить промпт: " + e.message;
  }
});
promptClose.addEventListener("click", () => promptOverlay.classList.add("hidden"));
promptOverlay.addEventListener("click", (e) => {
  if (e.target === promptOverlay) promptOverlay.classList.add("hidden");
});

// ---------------------- Доски (несколько полей с виджетами) ----------------------

async function loadBoards() {
  try {
    const res = await fetch("/api/boards");
    const data = await res.json();
    renderBoardsBar(data.boards || []);
  } catch (e) {
    // не критично для первого запуска
  }
}

function renderBoardsBar(boards) {
  boardsBarEl.innerHTML = "";
  for (const b of boards) {
    const tab = document.createElement("div");
    tab.className = "board-tab" + (b.id === currentBoardId ? " active" : "");
    tab.dataset.id = b.id;

    const label = document.createElement("span");
    label.className = "board-tab-label";
    label.textContent = b.name;
    tab.appendChild(label);

    if (boards.length > 1) {
      const closeBtn = document.createElement("button");
      closeBtn.className = "board-tab-close";
      closeBtn.innerHTML = "&times;";
      closeBtn.title = "Удалить доску";
      closeBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm(`Удалить доску «${b.name}» вместе со всеми её виджетами?`)) return;
        await fetch(`/api/boards/${b.id}`, { method: "DELETE" });
        if (currentBoardId === b.id) {
          currentBoardId = "default";
          localStorage.setItem("lastBoardId", currentBoardId);
        }
        await loadBoards();
        await switchToBoard(currentBoardId, { skipBarReload: true });
      });
      tab.appendChild(closeBtn);
    }

    tab.addEventListener("click", () => {
      if (b.id !== currentBoardId) switchToBoard(b.id);
    });
    tab.addEventListener("dblclick", async () => {
      const name = prompt("Новое название доски:", b.name);
      if (name && name.trim()) {
        await fetch(`/api/boards/${b.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name.trim() }),
        });
        await loadBoards();
      }
    });

    boardsBarEl.appendChild(tab);
  }

  const addBtn = document.createElement("button");
  addBtn.id = "add-board-btn";
  addBtn.title = "Новая доска";
  addBtn.textContent = "+";
  addBtn.addEventListener("click", async () => {
    const name = prompt("Название новой доски:", "Новая доска");
    if (name === null) return;
    const board = await (await fetch("/api/boards", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() || "Новая доска" }),
    })).json();
    await loadBoards();
    await switchToBoard(board.id);
  });
  boardsBarEl.appendChild(addBtn);
}

async function switchToBoard(boardId, { skipBarReload = false } = {}) {
  currentBoardId = boardId;
  localStorage.setItem("lastBoardId", boardId);

  // Полностью перерисовываем чат и холст под новую доску
  messagesEl.innerHTML = "";
  hideToolActivity();
  for (const [, entry] of widgetsById) entry.el.remove();
  widgetsById.clear();
  searchInput.value = "";

  resetView({ persist: false });
  loadViewForBoard(boardId);

  if (!skipBarReload) await loadBoards();
  await loadHistory();
  await loadWidgets();
}

// ---------------------- Инициализация ----------------------

async function init() {
  initSettingsUI();
  await loadModels();
  await loadBoards();
  loadViewForBoard(currentBoardId);
  applyZoom();
  await loadHistory();
  await loadWidgets();
}

async function loadModels() {
  try {
    const res = await fetch("/api/models");
    const data = await res.json();
    modelSelect.innerHTML = "";
    if (data.error || !data.models || data.models.length === 0) {
      const opt = document.createElement("option");
      opt.textContent = "Ollama недоступна";
      modelSelect.appendChild(opt);
      return;
    }
    for (const name of data.models) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      modelSelect.appendChild(opt);
    }
    currentModel = data.models[0];
    modelSelect.value = currentModel;
  } catch (e) {
    modelSelect.innerHTML = '<option>Ошибка загрузки моделей</option>';
  }
}

modelSelect.addEventListener("change", () => {
  currentModel = modelSelect.value;
});

async function loadHistory() {
  try {
    const res = await fetch(`/api/history?board_id=${encodeURIComponent(currentBoardId)}`);
    const data = await res.json();
    for (const item of data.history || []) {
      appendMessage(item.role, item.content);
    }
  } catch (e) {
    // тихо игнорируем — история не критична для запуска
  }
}

async function loadWidgets() {
  try {
    const res = await fetch(`/api/widgets?board_id=${encodeURIComponent(currentBoardId)}`);
    const data = await res.json();
    for (const w of data.widgets || []) {
      renderWidget(w, { animate: false });
    }
    updateCanvasHint();
    applySearchFilter();
  } catch (e) {
    // сервер мог ещё не подняться — не критично
  }
}

// ---------------------- Чат: отправка и SSE ----------------------

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = chatInput.value.trim();
  if (!text || isSending || !currentModel) return;

  appendMessage("user", text);
  chatInput.value = "";
  autoResize();
  await sendMessage(text);
});

chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.requestSubmit();
  }
});

chatInput.addEventListener("input", autoResize);

function autoResize() {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + "px";
}

function setSending(state) {
  isSending = state;
  sendBtn.disabled = state;
}

async function sendMessage(text) {
  setSending(true);
  showToolActivity("Думаю…");

  let assistantEl = null;
  let assistantText = "";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: currentModel, message: text, board_id: currentBoardId }),
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split("\n\n");
      buffer = parts.pop(); // последний кусок может быть неполным

      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        const jsonStr = line.slice(5).trim();
        if (!jsonStr) continue;

        let event;
        try {
          event = JSON.parse(jsonStr);
        } catch {
          continue;
        }

        if (event.type === "assistant_delta") {
          if (!assistantEl) {
            hideToolActivity();
            assistantEl = appendMessage("assistant", "", { streaming: true });
          }
          assistantText += event.text;
          updateStreamingMessage(assistantEl, assistantText);
        } else if (event.type === "tool_call") {
          showToolActivity(describeToolCall(event.tool, event.args));
        } else if (event.type === "tool_result") {
          // Оставляем индикатор активности, следующий шаг его обновит/скроет
        } else if (event.type === "widgets_update") {
          syncWidgets(event.widgets);
        } else if (event.type === "confirm_required") {
          showConfirmModal(event.id, event.command);
        } else if (event.type === "final") {
          hideToolActivity();
          if (assistantEl) {
            finalizeStreamingMessage(assistantEl, event.text);
          } else {
            appendMessage("assistant", event.text);
          }
          assistantText = event.text;
        } else if (event.type === "error") {
          hideToolActivity();
          appendMessage("assistant", "⚠ " + event.message);
        } else if (event.type === "done") {
          hideToolActivity();
        }
      }
    }
  } catch (e) {
    hideToolActivity();
    appendMessage("assistant", "⚠ Не удалось связаться с сервером: " + e.message);
  } finally {
    setSending(false);
  }
}

function describeToolCall(tool, args) {
  const labels = {
    web_search: `Ищу в интернете: «${args.query || ""}»`,
    open_url: `Открываю ссылку: ${args.url || ""}`,
    list_dir: `Смотрю содержимое папки: ${args.path || "."}`,
    read_file: `Читаю файл: ${args.path || ""}`,
    write_file: `Записываю файл: ${args.path || ""}`,
    run_command: `Хочу выполнить команду: ${args.command || ""}`,
    create_widget: `Создаю виджет: «${args.title || ""}»`,
    update_widget: `Обновляю виджет`,
    delete_widget: `Удаляю виджет`,
    list_widgets: `Смотрю список виджетов`,
    get_widget: `Читаю содержимое виджета целиком`,
  };
  return labels[tool] || `Вызываю инструмент: ${tool}`;
}

function showToolActivity(text) {
  toolActivityEl.classList.remove("hidden");
  toolActivityEl.innerHTML = `<div class="spinner"></div><span>${escapeHtml(text)}</span>`;
}

function hideToolActivity() {
  toolActivityEl.classList.add("hidden");
  toolActivityEl.innerHTML = "";
}

// ---------------------- Сообщения чата ----------------------

function appendMessage(role, text, opts = {}) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  if (opts.streaming) {
    const cursor = document.createElement("span");
    cursor.className = "cursor";
    el.appendChild(cursor);
  }
  messagesEl.appendChild(el);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return el;
}

function updateStreamingMessage(el, text) {
  el.innerHTML = "";
  el.appendChild(document.createTextNode(text));
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  el.appendChild(cursor);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function finalizeStreamingMessage(el, text) {
  el.innerHTML = "";
  el.appendChild(document.createTextNode(text));
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---------------------- Поиск по виджетам ----------------------

searchInput.addEventListener("input", applySearchFilter);

function applySearchFilter() {
  const q = searchInput.value.trim().toLowerCase();
  for (const [, entry] of widgetsById) {
    if (!q) {
      entry.el.classList.remove("search-hidden");
      continue;
    }
    const haystack = ((entry.data.title || "") + " " + (entry.data.content || "")).toLowerCase();
    entry.el.classList.toggle("search-hidden", !haystack.includes(q));
  }
}

// ---------------------- Пан и зум холста ----------------------

let view = { scale: 1, x: 0, y: 0 };

function viewKey(boardId) {
  return "canvasView:" + boardId;
}

function loadViewForBoard(boardId) {
  try {
    view = { ...{ scale: 1, x: 0, y: 0 }, ...JSON.parse(localStorage.getItem(viewKey(boardId)) || "{}") };
  } catch (e) {
    view = { scale: 1, x: 0, y: 0 };
  }
  applyZoom();
}

function saveView() {
  localStorage.setItem(viewKey(currentBoardId), JSON.stringify(view));
}

function applyZoom() {
  canvasEl.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
  zoomLevelEl.textContent = Math.round(view.scale * 100) + "%";
}

function resetView({ persist = true } = {}) {
  view = { scale: 1, x: 0, y: 0 };
  applyZoom();
  if (persist) saveView();
}

function zoomBy(factor, centerX, centerY) {
  const oldScale = view.scale;
  const newScale = Math.min(2.5, Math.max(0.3, oldScale * factor));
  const worldX = (centerX - view.x) / oldScale;
  const worldY = (centerY - view.y) / oldScale;
  view.x = centerX - worldX * newScale;
  view.y = centerY - worldY * newScale;
  view.scale = newScale;
  applyZoom();
  saveView();
}

canvasViewport.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvasViewport.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;
  const factor = e.deltaY > 0 ? 0.9 : 1.1;
  zoomBy(factor, cx, cy);
}, { passive: false });

zoomInBtn.addEventListener("click", () => {
  const rect = canvasViewport.getBoundingClientRect();
  zoomBy(1.2, rect.width / 2, rect.height / 2);
});
zoomOutBtn.addEventListener("click", () => {
  const rect = canvasViewport.getBoundingClientRect();
  zoomBy(1 / 1.2, rect.width / 2, rect.height / 2);
});
zoomResetBtn.addEventListener("click", () => resetView());

let isPanning = false, panStartX = 0, panStartY = 0, panOrigX = 0, panOrigY = 0;

canvasViewport.addEventListener("mousedown", (e) => {
  if (e.target.closest(".widget")) return; // клики по виджетам не двигают холст
  isPanning = true;
  panStartX = e.clientX;
  panStartY = e.clientY;
  panOrigX = view.x;
  panOrigY = view.y;
  canvasViewport.classList.add("panning");
});

window.addEventListener("mousemove", (e) => {
  if (!isPanning) return;
  view.x = panOrigX + (e.clientX - panStartX);
  view.y = panOrigY + (e.clientY - panStartY);
  applyZoom();
});

window.addEventListener("mouseup", () => {
  if (!isPanning) return;
  isPanning = false;
  canvasViewport.classList.remove("panning");
  saveView();
});

// ---------------------- Виджеты ----------------------

function widgetsUrl() {
  return `/api/widgets?board_id=${encodeURIComponent(currentBoardId)}`;
}
function widgetUrl(id, suffix = "") {
  return `/api/widgets/${id}${suffix}?board_id=${encodeURIComponent(currentBoardId)}`;
}

function updateCanvasHint() {
  canvasHint.classList.toggle("hidden", widgetsById.size > 0);
  widgetCountEl.textContent = widgetsById.size > 0 ? `${widgetsById.size} виджет(ов)` : "";
}

function syncWidgets(list) {
  const incomingIds = new Set(list.map((w) => w.id));

  // Удалённые
  for (const [id, entry] of widgetsById) {
    if (!incomingIds.has(id)) {
      animateOutAndRemove(entry.el);
      widgetsById.delete(id);
    }
  }

  // Новые / обновлённые
  for (const w of list) {
    const existing = widgetsById.get(w.id);
    if (!existing) {
      renderWidget(w, { animate: true });
    } else if (JSON.stringify(existing.data) !== JSON.stringify(w)) {
      const pending = pendingWidgetSaves.get(w.id);
      if (pending) {
        clearTimeout(pending.timer);
        pendingWidgetSaves.delete(w.id);
      }
      updateWidgetEl(existing.el, w);
      existing.data = w;
      pulseWidget(existing.el);
    }
  }
  updateCanvasHint();
  applySearchFilter();
}

function renderWidget(w, { animate }) {
  const el = document.createElement("div");
  el.className = "widget";
  el.style.left = (w.x || 40) + "px";
  el.style.top = (w.y || 40) + "px";
  el.style.width = (w.w || 320) + "px";
  el.style.height = (w.h || 220) + "px";
  el.dataset.id = w.id;

  const header = document.createElement("div");
  header.className = "widget-header";

  const title = document.createElement("span");
  title.className = "title";
  title.textContent = w.title || "Без названия";
  title.title = "Двойной клик — переименовать";
  title.addEventListener("dblclick", (e) => {
    e.stopPropagation();
    startRenameWidget(w.id, title);
  });

  const closeBtn = document.createElement("button");
  closeBtn.className = "widget-close";
  closeBtn.innerHTML = "&times;";
  closeBtn.title = "Удалить виджет";
  closeBtn.addEventListener("click", () => removeWidget(w.id));

  const saveDot = document.createElement("span");
  saveDot.className = "save-dot";
  saveDot.title = "Сохранено";

  header.appendChild(title);
  header.appendChild(saveDot);
  header.appendChild(closeBtn);

  const body = document.createElement("div");
  body.className = "widget-body";
  fillWidgetBody(body, w);

  const resizeHandle = document.createElement("div");
  resizeHandle.className = "widget-resize";
  resizeHandle.innerHTML = "◢";

  el.appendChild(header);
  el.appendChild(body);
  el.appendChild(resizeHandle);
  canvasEl.appendChild(el);

  makeDraggable(el, header, w.id);
  makeResizable(el, resizeHandle, w.id);

  widgetsById.set(w.id, { data: w, el, saveDot });

  if (animate) {
    el.classList.add("widget-enter");
    requestAnimationFrame(() => {
      el.classList.add("widget-enter-active");
      requestAnimationFrame(() => {
        el.classList.remove("widget-enter", "widget-enter-active");
      });
    });
  }
}

function startRenameWidget(id, titleEl) {
  const entry = widgetsById.get(id);
  const current = entry ? entry.data.title || "" : titleEl.textContent;

  const input = document.createElement("input");
  input.className = "title-edit";
  input.value = current;
  titleEl.replaceWith(input);
  input.focus();
  input.select();

  const finish = async (save) => {
    const newTitle = input.value.trim() || current;
    const span = document.createElement("span");
    span.className = "title";
    span.textContent = newTitle;
    span.title = "Двойной клик — переименовать";
    span.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      startRenameWidget(id, span);
    });
    input.replaceWith(span);

    if (save && newTitle !== current) {
      if (entry) entry.data.title = newTitle;
      try {
        await fetch(widgetUrl(id), {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: newTitle }),
        });
      } catch (e) {
        // не критично — досинхронизируется позже
      }
    }
  };

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(true));
  input.addEventListener("mousedown", (e) => e.stopPropagation());
}

function createContentEditor(id, initialContent, { mono = false, placeholder = "" } = {}) {
  const editor = document.createElement("textarea");
  editor.className = "content-editor" + (mono ? " mono" : "");
  editor.spellcheck = false;
  editor.value = initialContent || "";
  if (placeholder) editor.placeholder = placeholder;
  editor.dataset.widgetId = id;

  editor.addEventListener("mousedown", (e) => e.stopPropagation());
  editor.addEventListener("input", () => {
    scheduleSaveWidgetContent(id, editor.value);
  });
  editor.addEventListener("blur", () => {
    flushWidgetContent(id); // сразу, без ожидания задержки
  });
  editor.addEventListener("keydown", (e) => {
    // Tab вставляет отступ вместо перехода к следующему полю — удобно для кода
    if (e.key === "Tab") {
      e.preventDefault();
      const start = editor.selectionStart, end = editor.selectionEnd;
      editor.value = editor.value.slice(0, start) + "    " + editor.value.slice(end);
      editor.selectionStart = editor.selectionEnd = start + 4;
    }
  });

  return editor;
}

function fillWidgetBody(body, w) {
  body.innerHTML = "";
  body.classList.remove("html-body", "python-body", "text-body", "editor-body");
  body.classList.add("editor-body");

  if (w.type === "python") {
    body.classList.add("python-body");

    const editor = createContentEditor(w.id, w.content, { mono: true });

    const toolbar = document.createElement("div");
    toolbar.className = "code-toolbar";
    const runBtn = document.createElement("button");
    runBtn.className = "run-btn";
    runBtn.textContent = "▶ Запустить";

    const output = document.createElement("pre");
    output.className = "code-output";
    output.textContent = "Вывод появится здесь…";

    runBtn.addEventListener("click", () => runPythonCode(editor.value, output, runBtn));
    toolbar.appendChild(runBtn);

    body.appendChild(editor);
    body.appendChild(toolbar);
    body.appendChild(output);
  } else if (w.type === "html") {
    body.classList.add("html-body");

    const editor = createContentEditor(w.id, w.content, {
      mono: true,
      placeholder: "<!-- HTML/CSS/JS -->",
    });

    const preview = document.createElement("iframe");
    preview.className = "html-preview";
    preview.sandbox = "allow-scripts allow-popups allow-popups-to-escape-sandbox";
    preview.srcdoc = w.content || "";

    let previewTimer = null;
    editor.addEventListener("input", () => {
      clearTimeout(previewTimer);
      previewTimer = setTimeout(() => {
        preview.srcdoc = editor.value;
      }, 600);
    });

    const toolbar = document.createElement("div");
    toolbar.className = "code-toolbar";
    const toggleBtn = document.createElement("button");
    toggleBtn.className = "run-btn";
    toolbar.appendChild(toggleBtn);

    const splitWrap = document.createElement("div");
    splitWrap.className = "html-split";
    splitWrap.appendChild(editor);
    splitWrap.appendChild(preview);

    // По умолчанию — только просмотр (результат), код скрыт и открывается
    // по кнопке. Это и есть исправление жалобы "код виджета не убрать".
    function setMode(mode) {
      splitWrap.classList.toggle("mode-preview", mode === "preview");
      splitWrap.classList.toggle("mode-code", mode === "code");
      toggleBtn.textContent = mode === "preview" ? "✎ Код" : "👁 Просмотр";
    }
    toggleBtn.addEventListener("click", () => {
      setMode(splitWrap.classList.contains("mode-preview") ? "code" : "preview");
    });
    setMode("preview");

    body.appendChild(toolbar);
    body.appendChild(splitWrap);
  } else {
    body.classList.add("text-body");
    const editor = createContentEditor(w.id, w.content, {
      placeholder: "Пусто — впишите текст или попросите агента что-нибудь сюда добавить",
    });
    body.appendChild(editor);
  }
}

// Несохранённые правки — id виджета -> последнее непереданное содержимое.
const pendingWidgetSaves = new Map(); // id -> {content, timer}

function setSaveStatus(id, status) {
  const entry = widgetsById.get(id);
  if (!entry || !entry.saveDot) return;
  entry.saveDot.classList.remove("saving", "saved", "error");
  entry.saveDot.classList.add(status);
  entry.saveDot.title = {
    saving: "Сохранение…",
    saved: "Сохранено",
    error: "Не удалось сохранить — правки только в браузере! Проверьте, что сервер запущен, и консоль браузера (F12).",
  }[status] || "";
  if (status === "saved") {
    setTimeout(() => {
      if (entry.saveDot.classList.contains("saved")) entry.saveDot.classList.remove("saved");
    }, 1500);
  }
}

function scheduleSaveWidgetContent(id, content, delay = 600) {
  const existing = pendingWidgetSaves.get(id);
  if (existing && existing.timer) clearTimeout(existing.timer);
  const timer = setTimeout(() => flushWidgetContent(id), delay);
  pendingWidgetSaves.set(id, { content, timer });
  setSaveStatus(id, "saving");
}

async function flushWidgetContent(id) {
  const pending = pendingWidgetSaves.get(id);
  if (!pending) return;
  if (pending.timer) clearTimeout(pending.timer);
  pendingWidgetSaves.delete(id);
  setSaveStatus(id, "saving");

  try {
    const res = await fetch(widgetUrl(id), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: pending.content }),
    });
    if (!res.ok) {
      throw new Error(`Сервер ответил ${res.status} (${await res.text().catch(() => "")})`);
    }
    // Успех подтверждён сервером — только теперь считаем содержимое сохранённым.
    const entry = widgetsById.get(id);
    if (entry) entry.data.content = pending.content;
    setSaveStatus(id, "saved");
  } catch (e) {
    console.error("Не удалось сохранить виджет", id, e);
    setSaveStatus(id, "error");
    // Не теряем несохранённые правки — вернём их в очередь, попробуем снова
    // при следующем изменении (или их можно будет донести через sendBeacon).
    pendingWidgetSaves.set(id, pending);
  }
}

window.addEventListener("beforeunload", () => {
  for (const [id, pending] of pendingWidgetSaves) {
    const blob = new Blob([JSON.stringify({ content: pending.content })], {
      type: "application/json",
    });
    navigator.sendBeacon(widgetUrl(id, "/beacon"), blob);
  }
});

// ---------------------- Python-компилятор в браузере (Pyodide) ----------------------

let pyodideInstance = null;
let pyodideLoadingPromise = null;

function loadPyodideScript() {
  return new Promise((resolve, reject) => {
    if (window.loadPyodide) return resolve();
    const script = document.createElement("script");
    script.src = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("не удалось загрузить Pyodide (нужен интернет)"));
    document.head.appendChild(script);
  });
}

async function ensurePyodide() {
  if (pyodideInstance) return pyodideInstance;
  if (!pyodideLoadingPromise) {
    pyodideLoadingPromise = (async () => {
      await loadPyodideScript();
      pyodideInstance = await window.loadPyodide();
      return pyodideInstance;
    })();
  }
  return pyodideLoadingPromise;
}

async function runPythonCode(code, outputEl, runBtn) {
  runBtn.disabled = true;
  outputEl.textContent = "Загружаю интерпретатор Python (в первый раз может занять до минуты)…";
  try {
    const pyodide = await ensurePyodide();
    outputEl.textContent = "Выполняю…";

    pyodide.globals.set("_widget_code", code);
    const wrapper = [
      "import sys, io, traceback",
      "_buf = io.StringIO()",
      "_old_stdout = sys.stdout",
      "sys.stdout = _buf",
      "try:",
      "    exec(_widget_code, {})",
      "except Exception:",
      "    traceback.print_exc()",
      "finally:",
      "    sys.stdout = _old_stdout",
      "_buf.getvalue()",
    ].join("\n");

    const result = await pyodide.runPythonAsync(wrapper);
    outputEl.textContent = result && result.length ? result : "(нет вывода)";
  } catch (e) {
    outputEl.textContent = "Ошибка запуска: " + e.message;
  } finally {
    runBtn.disabled = false;
  }
}

function updateWidgetEl(el, w) {
  const titleNode = el.querySelector(".title") || el.querySelector(".title-edit");
  if (titleNode) titleNode.textContent = w.title || "Без названия";
  el.style.left = (w.x || 0) + "px";
  el.style.top = (w.y || 0) + "px";
  el.style.width = (w.w || 320) + "px";
  el.style.height = (w.h || 220) + "px";
  fillWidgetBody(el.querySelector(".widget-body"), w);
}

function pulseWidget(el) {
  el.classList.remove("widget-pulse");
  void el.offsetWidth; // форсируем reflow, чтобы анимация перезапустилась
  el.classList.add("widget-pulse");
}

function animateOutAndRemove(el) {
  el.classList.add("widget-exit");
  setTimeout(() => el.remove(), 220);
}

async function removeWidget(id) {
  const entry = widgetsById.get(id);
  if (entry) {
    animateOutAndRemove(entry.el);
    widgetsById.delete(id);
    updateCanvasHint();
  }
  try {
    await fetch(widgetUrl(id), { method: "DELETE" });
  } catch (e) {
    // если не получилось на сервере — она вернётся при следующей синхронизации
  }
}

function makeDraggable(el, handle, id) {
  let startX, startY, origX, origY, dragging = false;

  handle.addEventListener("mousedown", (e) => {
    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    origX = el.offsetLeft;
    origY = el.offsetTop;
    el.classList.add("dragging");
    e.preventDefault();
    e.stopPropagation();
  });

  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const dx = (e.clientX - startX) / view.scale;
    const dy = (e.clientY - startY) / view.scale;
    el.style.left = Math.max(0, origX + dx) + "px";
    el.style.top = Math.max(0, origY + dy) + "px";
  });

  window.addEventListener("mouseup", async () => {
    if (!dragging) return;
    dragging = false;
    el.classList.remove("dragging");
    const entry = widgetsById.get(id);
    if (entry) {
      entry.data.x = el.offsetLeft;
      entry.data.y = el.offsetTop;
    }
    try {
      await fetch(widgetUrl(id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ x: el.offsetLeft, y: el.offsetTop }),
      });
    } catch (e) {
      // ладно, позиция обновится при следующей полной синхронизации
    }
  });
}

function makeResizable(el, handle, id) {
  let startX, startY, origW, origH, resizing = false;

  handle.addEventListener("mousedown", (e) => {
    resizing = true;
    startX = e.clientX;
    startY = e.clientY;
    origW = el.offsetWidth;
    origH = el.offsetHeight;
    e.preventDefault();
    e.stopPropagation();
  });

  window.addEventListener("mousemove", (e) => {
    if (!resizing) return;
    const dw = (e.clientX - startX) / view.scale;
    const dh = (e.clientY - startY) / view.scale;
    el.style.width = Math.max(180, origW + dw) + "px";
    el.style.height = Math.max(120, origH + dh) + "px";
  });

  window.addEventListener("mouseup", async () => {
    if (!resizing) return;
    resizing = false;
    const entry = widgetsById.get(id);
    if (entry) {
      entry.data.w = el.offsetWidth;
      entry.data.h = el.offsetHeight;
    }
    try {
      await fetch(widgetUrl(id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ w: el.offsetWidth, h: el.offsetHeight }),
      });
    } catch (e) {
      // не критично
    }
  });
}

// ---------------------- Подтверждение команд ----------------------

function showConfirmModal(id, command) {
  pendingConfirmId = id;
  confirmCommandEl.textContent = command;
  confirmOverlay.classList.remove("hidden");
}

function hideConfirmModal() {
  confirmOverlay.classList.add("hidden");
  pendingConfirmId = null;
}

async function respondToConfirm(approved) {
  if (!pendingConfirmId) return;
  const id = pendingConfirmId;
  hideConfirmModal();
  try {
    await fetch("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, approved }),
    });
  } catch (e) {
    appendMessage("assistant", "⚠ Не удалось отправить подтверждение: " + e.message);
  }
}

confirmAllowBtn.addEventListener("click", () => respondToConfirm(true));
confirmDenyBtn.addEventListener("click", () => respondToConfirm(false));

// ---------------------- Старт ----------------------

init();
