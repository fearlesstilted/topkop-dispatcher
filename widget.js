/**
 * TOP KOP Chat Widget v4.0
 * Vanilla JS — no dependencies.
 * Connects to Gradio 6 backend via Queue API (SSE streaming).
 *
 * Changes vs v3:
 *   - Shadow DOM — виджет изолирован от CSS родительского сайта (WordPress/Webflow)
 *   - Баг-фикс: raw[0]?.text → raw.map(b=>b?.text??'').join('') (Gradio 6.11 контент-блоки)
 *   - Quick replies удалены (owner decision)
 *   - Google Fonts инжектируется в <head>, а не в shadow (cross-origin @import)
 *
 * Usage:
 *   <script defer src="widget.js" data-endpoint="https://your-gradio-share.live"></script>
 */

(function () {
  "use strict";

  /* ─── CONFIG ─────────────────────────────────────────────────────────────── */
  const currentScript = document.currentScript;
  const ENDPOINT =
    (currentScript && currentScript.dataset.endpoint) ||
    "http://127.0.0.1:7860";

  const FN_INDEX = 0;
  const TYPING_DELAY_MS = 600;

  /* ─── STATE ──────────────────────────────────────────────────────────────── */
  let chatHistory = []; // [{role:"user"|"assistant", content:"..."}]
  let isWaiting   = false;
  let sessionHash = Math.random().toString(36).slice(2);

  /* ─── STYLES (живут внутри Shadow DOM) ──────────────────────────────────── */
  const CSS = `
    :host {
      --tk-orange:     #e07b00;
      --tk-orange-dim: #c46d00;
      --tk-dark:       #1a1a1a;
      --tk-surface:    #242424;
      --tk-border:     #333;
      --tk-text:       #e8e8e8;
      --tk-subtext:    #888;
      --tk-user-bg:    #e07b00;
      --tk-bot-bg:     #2d2d2d;
      --tk-radius:     14px;
      --tk-shadow:     0 20px 60px rgba(0,0,0,.55);
      --tk-font:       'Barlow', system-ui, sans-serif;
      --tk-font-head:  'Barlow Condensed', sans-serif;
    }

    #tk-fab {
      position: fixed;
      bottom: 28px;
      right: 28px;
      z-index: 9999;
      width: 60px;
      height: 60px;
      border-radius: 50%;
      background: var(--tk-orange);
      border: none;
      cursor: pointer;
      box-shadow: 0 6px 24px rgba(224,123,0,.55);
      display: flex;
      align-items: center;
      justify-content: center;
      transition: transform .2s, box-shadow .2s;
    }
    #tk-fab:hover {
      transform: scale(1.08);
      box-shadow: 0 10px 32px rgba(224,123,0,.7);
    }
    #tk-fab svg { pointer-events: none; }

    #tk-badge {
      position: absolute;
      top: -4px;
      right: -4px;
      width: 18px;
      height: 18px;
      background: #e84040;
      border-radius: 50%;
      font-size: 10px;
      font-weight: 700;
      color: #fff;
      display: none;
      align-items: center;
      justify-content: center;
      font-family: var(--tk-font);
    }

    #tk-window {
      position: fixed;
      bottom: 104px;
      right: 28px;
      z-index: 9998;
      width: 380px;
      max-width: calc(100vw - 40px);
      height: 560px;
      max-height: calc(100vh - 140px);
      background: var(--tk-dark);
      border: 1px solid var(--tk-border);
      border-radius: var(--tk-radius);
      box-shadow: var(--tk-shadow);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      font-family: var(--tk-font);
      transition: opacity .25s, transform .25s;
      transform-origin: bottom right;
    }
    #tk-window.tk-hidden {
      opacity: 0;
      transform: scale(.92) translateY(10px);
      pointer-events: none;
    }

    /* Header */
    #tk-header {
      background: linear-gradient(135deg, #1f1f1f 0%, #2a1800 100%);
      border-bottom: 1px solid var(--tk-border);
      padding: 14px 16px;
      display: flex;
      align-items: center;
      gap: 12px;
      flex-shrink: 0;
    }
    #tk-header-avatar {
      width: 38px;
      height: 38px;
      border-radius: 50%;
      background: var(--tk-orange);
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    #tk-header-info { flex: 1; min-width: 0; }
    #tk-header-title {
      font-family: var(--tk-font-head);
      font-size: 15px;
      font-weight: 700;
      color: #fff;
      letter-spacing: .5px;
      line-height: 1;
    }
    #tk-header-status {
      font-size: 11px;
      color: var(--tk-subtext);
      margin-top: 3px;
      display: flex;
      align-items: center;
      gap: 5px;
    }
    #tk-status-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #4caf50;
      flex-shrink: 0;
      box-shadow: 0 0 6px #4caf5099;
    }
    #tk-status-dot.offline { background: #888; box-shadow: none; }
    #tk-status-dot.busy    { background: var(--tk-orange); }

    #tk-close {
      background: transparent;
      border: none;
      color: var(--tk-subtext);
      cursor: pointer;
      padding: 4px;
      border-radius: 6px;
      display: flex;
      transition: color .15s, background .15s;
    }
    #tk-close:hover { color: #fff; background: #333; }

    /* Messages */
    #tk-messages {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      scroll-behavior: smooth;
    }
    #tk-messages::-webkit-scrollbar { width: 4px; }
    #tk-messages::-webkit-scrollbar-thumb { background: #444; border-radius: 4px; }

    .tk-msg-row {
      display: flex;
      align-items: flex-end;
      gap: 8px;
      animation: tk-fade-in .2s ease;
    }
    .tk-msg-row.user { flex-direction: row-reverse; }

    @keyframes tk-fade-in {
      from { opacity: 0; transform: translateY(6px); }
      to   { opacity: 1; transform: translateY(0); }
    }

    .tk-avatar-sm {
      width: 26px;
      height: 26px;
      border-radius: 50%;
      background: var(--tk-orange);
      flex-shrink: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 11px;
      font-weight: 700;
      color: #fff;
      font-family: var(--tk-font-head);
    }
    .tk-msg-row.user .tk-avatar-sm { background: #3a3a3a; }

    .tk-bubble {
      max-width: 78%;
      padding: 9px 13px;
      border-radius: 14px;
      font-size: 13.5px;
      line-height: 1.5;
      word-break: break-word;
      white-space: pre-wrap;
    }
    .tk-msg-row.bot  .tk-bubble {
      background: var(--tk-bot-bg);
      color: var(--tk-text);
      border-bottom-left-radius: 4px;
    }
    .tk-msg-row.user .tk-bubble {
      background: var(--tk-user-bg);
      color: #fff;
      border-bottom-right-radius: 4px;
    }

    .tk-timestamp {
      font-size: 10px;
      color: var(--tk-subtext);
      margin-top: 2px;
      text-align: right;
    }
    .tk-msg-row.bot .tk-timestamp { text-align: left; }

    /* Typing indicator */
    #tk-typing {
      display: none;
      align-items: flex-end;
      gap: 8px;
      padding: 0 16px 8px;
    }
    #tk-typing.visible { display: flex; }
    .tk-dots {
      background: var(--tk-bot-bg);
      border-radius: 14px;
      border-bottom-left-radius: 4px;
      padding: 10px 14px;
      display: flex;
      gap: 5px;
    }
    .tk-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--tk-subtext);
      animation: tk-bounce .9s infinite;
    }
    .tk-dot:nth-child(2) { animation-delay: .15s; }
    .tk-dot:nth-child(3) { animation-delay: .30s; }
    @keyframes tk-bounce {
      0%,80%,100% { transform: translateY(0); }
      40%         { transform: translateY(-6px); background: var(--tk-orange); }
    }

    /* Input area */
    #tk-footer {
      border-top: 1px solid var(--tk-border);
      padding: 12px;
      display: flex;
      gap: 8px;
      align-items: flex-end;
      flex-shrink: 0;
    }
    #tk-input {
      flex: 1;
      background: var(--tk-surface);
      border: 1px solid var(--tk-border);
      border-radius: 10px;
      color: var(--tk-text);
      font-family: var(--tk-font);
      font-size: 13.5px;
      padding: 9px 12px;
      resize: none;
      outline: none;
      max-height: 100px;
      min-height: 38px;
      line-height: 1.4;
      transition: border-color .2s;
      scrollbar-width: none;
    }
    #tk-input:focus { border-color: var(--tk-orange); }
    #tk-input::placeholder { color: #555; }

    #tk-send {
      width: 40px;
      height: 40px;
      border-radius: 10px;
      background: var(--tk-orange);
      border: none;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      transition: background .15s, transform .1s;
    }
    #tk-send:hover:not(:disabled) { background: var(--tk-orange-dim); }
    #tk-send:active:not(:disabled) { transform: scale(.94); }
    #tk-send:disabled { background: #444; cursor: default; }

    /* Error toast */
    .tk-error-bubble {
      background: #3a1010;
      border: 1px solid #6b2020;
      color: #ffaaaa;
      font-size: 12.5px;
      padding: 7px 12px;
      border-radius: 10px;
      max-width: 90%;
    }

    /* Responsive */
    @media (max-width: 420px) {
      #tk-window { right: 0; bottom: 80px; width: 100vw; border-radius: 14px 14px 0 0; }
      #tk-fab    { bottom: 20px; right: 16px; }
    }
  `;

  /* ─── DOM BUILDER (Shadow DOM) ───────────────────────────────────────────── */
  function buildWidget() {
    // Шрифт грузим в основной документ — @import в shadow root блокируется в ряде браузеров
    if (!document.querySelector("#tk-font-link")) {
      const link = document.createElement("link");
      link.id   = "tk-font-link";
      link.rel  = "stylesheet";
      link.href = "https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&family=Barlow+Condensed:wght@700&display=swap";
      document.head.appendChild(link);
    }

    // Host — пустой div, якорь для Shadow root
    const host = document.createElement("div");
    document.body.appendChild(host);

    // Shadow root изолирует все наши стили от CSS родительского сайта
    const shadow = host.attachShadow({ mode: "open" });

    const styleEl = document.createElement("style");
    styleEl.textContent = CSS;
    shadow.appendChild(styleEl);

    // FAB button
    const fab = document.createElement("button");
    fab.id = "tk-fab";
    fab.setAttribute("aria-label", "Otwórz czat TOP KOP");
    fab.innerHTML = `
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      </svg>
      <span id="tk-badge"></span>`;

    // Chat window
    const win = document.createElement("div");
    win.id = "tk-window";
    win.className = "tk-hidden";
    win.setAttribute("role", "dialog");
    win.setAttribute("aria-label", "Czat TOP KOP");
    win.innerHTML = `
      <div id="tk-header">
        <div id="tk-header-avatar">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M2 20h.01M7 20v-4a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v4M22 20H2M12 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM17 20v-6"/>
            <path d="M22 20v-3a2 2 0 0 0-2-2h-1"/>
          </svg>
        </div>
        <div id="tk-header-info">
          <div id="tk-header-title">TOP KOP · Dyspozytor</div>
          <div id="tk-header-status">
            <span id="tk-status-dot"></span>
            <span id="tk-status-text">Dostępny</span>
          </div>
        </div>
        <button id="tk-close" aria-label="Zamknij">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>

      <div id="tk-messages" role="log" aria-live="polite"></div>

      <div id="tk-typing" role="status" aria-label="Dyspozytor pisze...">
        <div class="tk-avatar-sm">TK</div>
        <div class="tk-dots">
          <div class="tk-dot"></div>
          <div class="tk-dot"></div>
          <div class="tk-dot"></div>
        </div>
      </div>

      <div id="tk-footer">
        <textarea id="tk-input" rows="1" placeholder="Napisz zapytanie..." aria-label="Wpisz wiadomość"></textarea>
        <button id="tk-send" aria-label="Wyślij">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
          </svg>
        </button>
      </div>
    `;

    shadow.appendChild(fab);
    shadow.appendChild(win);

    return {
      fab, win,
      messages:   win.querySelector("#tk-messages"),
      typing:     win.querySelector("#tk-typing"),
      input:      win.querySelector("#tk-input"),
      send:       win.querySelector("#tk-send"),
      close:      win.querySelector("#tk-close"),
      badge:      fab.querySelector("#tk-badge"),
      statusDot:  win.querySelector("#tk-status-dot"),
      statusText: win.querySelector("#tk-status-text"),
    };
  }

  /* ─── RENDER HELPERS ─────────────────────────────────────────────────────── */
  function nowHM() {
    return new Date().toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
  }

  function appendMessage(el, role, text, isError = false) {
    const row = document.createElement("div");
    row.className = `tk-msg-row ${role}`;

    const avatar = document.createElement("div");
    avatar.className = "tk-avatar-sm";
    avatar.textContent = role === "user" ? "Ty" : "TK";

    const bubble = document.createElement("div");
    bubble.className = isError ? "tk-error-bubble" : "tk-bubble";
    bubble.textContent = text; // plain text — no innerHTML, safe against XSS

    const ts = document.createElement("div");
    ts.className = "tk-timestamp";
    ts.textContent = nowHM();

    const inner = document.createElement("div");
    inner.style.maxWidth = "80%";
    inner.appendChild(bubble);
    inner.appendChild(ts);

    row.appendChild(avatar);
    row.appendChild(inner);
    el.appendChild(row);
    el.scrollTop = el.scrollHeight;
    return bubble; // returned for live streaming updates
  }

  function setStatus(dot, textEl, state) {
    dot.className = "";
    const map = {
      online:  { cls: "",        label: "Dostępny" },
      busy:    { cls: "busy",    label: "Odpowiada..." },
      offline: { cls: "offline", label: "Niedostępny" },
    };
    const s = map[state] || map.online;
    if (s.cls) dot.classList.add(s.cls);
    textEl.textContent = s.label;
  }

  /* ─── GRADIO 6 QUEUE API (SSE STREAMING) ─────────────────────────────────── */
  /**
   * Gradio 6 API paths use /gradio_api/ prefix.
   *
   * Flow:
   *   POST /gradio_api/queue/join   → { event_id }
   *   GET  /gradio_api/queue/data?session_hash=  → SSE stream
   *
   * SSE event types:
   *   "heartbeat"          → ignore
   *   "estimation"         → queue position, optional
   *   "process_generating" → streaming chunk, extract from payload.output.data[1]
   *   "process_completed"  → final result, same structure
   *   "queue_full"         → error
   *   "error"              → error
   */
  async function sendToGradio(userMsg, onChunk, onDone, onError) {
    // Gradio 6.11 ChatbotDataMessages: content must be list, not string
    const gradioHistory = chatHistory.map((m) => ({
      role: m.role,
      content: [{ type: "text", text: m.content }],
    }));

    // ── 1. Join the queue ─────────────────────────────────────────────────────
    let eventId;
    try {
      const joinResp = await fetch(`${ENDPOINT}/gradio_api/queue/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          data: [userMsg, gradioHistory],
          fn_index: FN_INDEX,
          session_hash: sessionHash,
          event_data: null,
        }),
      });

      if (!joinResp.ok) {
        throw new Error(`HTTP ${joinResp.status}: ${joinResp.statusText}`);
      }
      const joinData = await joinResp.json();
      eventId = joinData.event_id;
      if (!eventId) throw new Error("Brak event_id w odpowiedzi serwera.");
    } catch (err) {
      onError(`Błąd połączenia: ${err.message}`);
      return;
    }

    // ── 2. Subscribe to SSE stream ────────────────────────────────────────────
    const evtUrl = `${ENDPOINT}/gradio_api/queue/data?session_hash=${sessionHash}`;
    const evtSource = new EventSource(evtUrl);
    let doneFired = false;

    evtSource.onmessage = (ev) => {
      let payload;
      try { payload = JSON.parse(ev.data); } catch { return; }

      // Filter events from other concurrent sessions
      if (payload.event_id && payload.event_id !== eventId) return;

      const type = payload.msg;

      if (type === "heartbeat" || type === "estimation") return;

      // ── Streaming chunk ────────────────────────────────────────────────────
      // Gradio 6 structure for a generator that yields ("", history):
      //   payload.output.data[0] = ""           (cleared textbox)
      //   payload.output.data[1] = list[dict]   (full history so far)
      //   last item in history = {"role": "assistant", "content": "<partial text>"}
      if (type === "process_generating") {
        try {
          const historyArr = payload?.output?.data?.[1];
          if (Array.isArray(historyArr) && historyArr.length > 0) {
            const lastMsg = historyArr[historyArr.length - 1];
            const raw = lastMsg?.content ?? "";
            // Gradio 6.11 может вернуть content как массив блоков [{type,text},...].
            // Берём ВСЕ блоки и склеиваем — иначе покажем только первый символ.
            const text = Array.isArray(raw)
              ? raw.map((b) => b?.text ?? "").join("")
              : raw;
            if (text) onChunk(text);
          }
        } catch (_) {}
        return;
      }

      // ── Final result ───────────────────────────────────────────────────────
      if (type === "process_completed") {
        evtSource.close();
        doneFired = true;
        try {
          const historyArr = payload?.output?.data?.[1];
          if (Array.isArray(historyArr) && historyArr.length > 0) {
            // Sync local widget history with Gradio's authoritative state
            chatHistory = historyArr.map((m) => {
              const c = m.content || "";
              // Та же логика: склеиваем все блоки если content — массив
              const text = Array.isArray(c)
                ? c.map((b) => b?.text ?? "").join("")
                : c;
              return { role: m.role, content: text };
            });
            const rawFinal = historyArr[historyArr.length - 1]?.content || "";
            const finalText = Array.isArray(rawFinal)
              ? rawFinal.map((b) => b?.text ?? "").join("")
              : rawFinal;
            onDone(finalText);
          } else {
            onDone("");
          }
        } catch (e) {
          onDone("");
        }
        return;
      }

      if (type === "queue_full") {
        evtSource.close();
        onError("Kolejka zapytań jest pełna. Spróbuj ponownie za chwilę.");
        return;
      }

      if (type === "error" || payload.error) {
        evtSource.close();
        onError(payload.error || "Nieznany błąd serwera.");
      }
    };

    evtSource.onerror = () => {
      evtSource.close();
      // Suppress error if we already fired onDone — normal SSE close behavior
      if (!doneFired) {
        onError("Połączenie SSE zostało przerwane.");
      }
    };
  }

  /* ─── MAIN CONTROLLER ────────────────────────────────────────────────────── */
  function init() {
    const UI = buildWidget();
    let isOpen = false;
    let unread = 0;

    // Greeting message
    appendMessage(
      UI.messages,
      "bot",
      "Witam! Jestem dyspozytorem TOP KOP. W czym mogę pomóc? Oferujemy roboty ziemne, transport kruszywa, wynajem sprzętu i wiele więcej."
    );

    // Глобальный хук — кнопки на сайте могут вызвать window.tkOpen() напрямую
    // (Shadow DOM скрывает #tk-fab от document.getElementById)
    window.tkOpen  = () => openChat();
    window.tkClose = () => closeChat();

    function openChat() {
      isOpen = true;
      UI.win.classList.remove("tk-hidden");
      UI.fab.setAttribute("aria-expanded", "true");
      UI.input.focus();
      unread = 0;
      UI.badge.style.display = "none";
    }

    function closeChat() {
      isOpen = false;
      UI.win.classList.add("tk-hidden");
      UI.fab.setAttribute("aria-expanded", "false");
    }

    UI.fab.addEventListener("click", () => (isOpen ? closeChat() : openChat()));
    UI.close.addEventListener("click", closeChat);

    // Escape закрывает — событие идёт от document, пробивает shadow boundary
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && isOpen) closeChat();
    });

    UI.input.addEventListener("input", () => {
      UI.input.style.height = "auto";
      UI.input.style.height = Math.min(UI.input.scrollHeight, 100) + "px";
    });

    UI.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        triggerSend();
      }
    });

    UI.send.addEventListener("click", triggerSend);

    function triggerSend() {
      const msg = UI.input.value.trim();
      if (!msg || isWaiting) return;
      sendMessage(msg);
    }

    function sendMessage(text) {
      if (!text.trim() || isWaiting) return;

      UI.input.value = "";
      UI.input.style.height = "auto";

      appendMessage(UI.messages, "user", text);

      chatHistory.push({ role: "user", content: text });

      isWaiting = true;
      UI.send.disabled = true;
      setStatus(UI.statusDot, UI.statusText, "busy");

      setTimeout(() => {
        if (isWaiting) UI.typing.classList.add("visible");
        UI.messages.scrollTop = UI.messages.scrollHeight;
      }, TYPING_DELAY_MS);

      let botBubble = null;
      let firstChunk = true;

      sendToGradio(
        text,
        /* onChunk */ (partialText) => {
          if (firstChunk) {
            firstChunk = false;
            UI.typing.classList.remove("visible");
            botBubble = appendMessage(UI.messages, "bot", partialText);
          } else if (botBubble) {
            botBubble.textContent = partialText; // заменяем полным накопленным текстом
            UI.messages.scrollTop = UI.messages.scrollHeight;
          }
        },
        /* onDone */ (finalText) => {
          UI.typing.classList.remove("visible");
          if (!botBubble) {
            appendMessage(UI.messages, "bot", finalText);
          } else {
            botBubble.textContent = finalText;
          }
          UI.messages.scrollTop = UI.messages.scrollHeight;
          unlockUI();

          if (!isOpen) {
            unread++;
            UI.badge.textContent = unread;
            UI.badge.style.display = "flex";
          }
        },
        /* onError */ (errMsg) => {
          UI.typing.classList.remove("visible");
          appendMessage(UI.messages, "bot", errMsg, true);
          unlockUI();
        }
      );
    }

    function unlockUI() {
      isWaiting = false;
      UI.send.disabled = false;
      setStatus(UI.statusDot, UI.statusText, "online");
      UI.input.focus();
    }
  }

  /* ─── BOOT ───────────────────────────────────────────────────────────────── */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
