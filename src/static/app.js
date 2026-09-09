const app = document.querySelector("#app");
const messages = document.querySelector("#messages");
const empty = document.querySelector("#empty");
const form = document.querySelector("#composer");
const input = document.querySelector("#message");
const status = document.querySelector("#status");
const statusDot = document.querySelector("#status-dot");
const model = document.querySelector("#model");
const send = document.querySelector("#send-btn");
const menuToggle = document.querySelector("#menu-toggle");
const drawer = document.querySelector("#drawer");
const drawerClose = document.querySelector("#drawer-close");
const drawerOverlay = document.querySelector("#drawer-overlay");
const drawerNewChat = document.querySelector("#drawer-new-chat");
const sessionList = document.querySelector("#session-list");
const archivedList = document.querySelector("#archived-list");
const archivedToggle = document.querySelector("#archived-toggle");
const sessionStatus = document.querySelector("#session-status");
let busy = true;
let focusBeforeDrawer = null;
let viewportFrame = 0;
let activeSessionId = null;
let expandedSessionId = null;
let renamingSessionId = null;
let archivedOpen = false;
let knownSessions = [];
const sessionDate = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Jakarta",
});

function setStatus(value) {
  status.textContent = value;
  statusDot.classList.toggle("cold", value === "Connecting");
  statusDot.classList.toggle("thinking", value === "Thinking");
  statusDot.classList.toggle("offline", value === "Offline");
  statusDot.title = value;
}

function setModel(alias) {
  model.textContent = alias ? alias.split("/").pop().replace(":free", "") : "";
}

function updateSendState() {
  send.disabled = busy || !input.value.trim();
}

function syncVisualViewport() {
  const viewport = window.visualViewport;
  const height = viewport?.height || window.innerHeight;
  const top = viewport?.offsetTop || 0;
  document.documentElement.style.setProperty("--viewport-height", `${height}px`);
  document.documentElement.style.setProperty("--viewport-top", `${top}px`);
  document.body.classList.toggle("keyboard-open", Boolean(viewport && window.innerHeight - height > 120));
  scrollToLatest();
}

function scheduleViewportSync() {
  cancelAnimationFrame(viewportFrame);
  viewportFrame = requestAnimationFrame(syncVisualViewport);
}

function setDrawerOpen(open) {
  if (open) focusBeforeDrawer = document.activeElement;
  drawer.classList.toggle("open", open);
  drawerOverlay.classList.toggle("open", open);
  document.body.classList.toggle("drawer-open", open);
  menuToggle.setAttribute("aria-expanded", String(open));
  menuToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  drawer.setAttribute("aria-hidden", String(!open));
  app.inert = open;
  if (open) {
    loadSessions().catch(() => { sessionStatus.textContent = "Chats unavailable."; });
    setTimeout(() => drawerClose.focus(), 0);
  } else if (focusBeforeDrawer instanceof HTMLElement) {
    focusBeforeDrawer.focus();
  }
}

function resizeComposer() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

function scrollToLatest() {
  messages.scrollTop = messages.scrollHeight;
}

function add(event) {
  empty.hidden = true;
  const article = document.createElement("article");
  article.className = event.role;
  article.classList.toggle("waiting", event.kind === "waiting");
  const text = document.createElement("p");
  text.textContent = event.content || "";
  article.append(text);
  let indicator = null;
  if (event.responding) {
    article.classList.add("responding", "typing");
    indicator = document.createElement("span");
    indicator.className = "response-loader";
    indicator.setAttribute("role", "status");
    indicator.setAttribute("aria-label", "Sage is responding");
    indicator.replaceChildren(...Array.from({length: 3}, () => document.createElement("i")));
    article.append(indicator);
  }
  messages.append(article);
  scrollToLatest();
  return {article, text, indicator};
}

function clearConversation() {
  messages.querySelectorAll("article").forEach((article) => article.remove());
  empty.hidden = false;
}

async function loadHistory() {
  const response = await fetch("/api/history");
  if (!response.ok) throw new Error("history unavailable");
  const {events, model: alias, session_id: sessionId} = await response.json();
  clearConversation();
  activeSessionId = sessionId;
  setModel(alias);
  for (const event of events || []) add(event);
  setStatus("Ready");
}

function sessionMeta(session) {
  const count = `${session.event_count} ${session.event_count === 1 ? "message" : "messages"}`;
  let date = "";
  try {
    date = `${sessionDate.format(new Date(session.last_active_at))} WIB`;
  } catch {
    date = "Date unavailable";
  }
  return `${session.active ? "Current · " : ""}${date} · ${count}`;
}

function sessionRow(session, archived = false) {
  const item = document.createElement("div");
  item.className = "session-item";
  item.classList.toggle("active", session.active);
  item.setAttribute("role", "listitem");

  const main = document.createElement("div");
  main.className = "session-main";
  const open = document.createElement(archived ? "div" : "button");
  open.className = "session-open";
  if (!archived) {
    open.type = "button";
    open.dataset.sessionAction = "open";
    open.dataset.sessionId = session.id;
    if (session.active) open.setAttribute("aria-current", "page");
  }
  const title = document.createElement("span");
  title.className = "session-title";
  title.textContent = session.title;
  const meta = document.createElement("span");
  meta.className = "session-meta";
  meta.textContent = sessionMeta(session);
  open.append(title, meta);
  main.append(open);

  if (archived) {
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "session-restore";
    restore.dataset.sessionAction = "unarchive";
    restore.dataset.sessionId = session.id;
    restore.textContent = "Restore";
    restore.setAttribute("aria-label", `Restore ${session.title}`);
    main.append(restore);
  } else {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "session-more";
    more.dataset.sessionAction = "more";
    more.dataset.sessionId = session.id;
    more.setAttribute("aria-label", `More actions for ${session.title}`);
    more.setAttribute("aria-expanded", String(expandedSessionId === session.id));
    more.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>';
    main.append(more);
  }
  item.append(main);

  if (!archived && expandedSessionId === session.id) {
    if (renamingSessionId === session.id) {
      const rename = document.createElement("form");
      rename.className = "session-rename";
      rename.dataset.sessionId = session.id;
      const label = document.createElement("label");
      label.className = "sr-only";
      label.htmlFor = `session-title-${session.id}`;
      label.textContent = "Chat title";
      const renameInput = document.createElement("input");
      renameInput.id = label.htmlFor;
      renameInput.name = "title";
      renameInput.value = session.title;
      renameInput.maxLength = 120;
      renameInput.required = true;
      const save = document.createElement("button");
      save.type = "submit";
      save.textContent = "Save";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.dataset.sessionAction = "cancel-rename";
      cancel.dataset.sessionId = session.id;
      cancel.textContent = "Cancel";
      rename.append(label, renameInput, save, cancel);
      item.append(rename);
    } else {
      const actions = document.createElement("div");
      actions.className = "session-actions";
      for (const [action, label] of [["rename", "Rename"], ["archive", "Archive"]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.sessionAction = action;
        button.dataset.sessionId = session.id;
        button.textContent = label;
        actions.append(button);
      }
      item.append(actions);
    }
  }
  return item;
}

function renderSessions(sessions) {
  knownSessions = sessions;
  sessionList.replaceChildren();
  archivedList.replaceChildren();
  const current = sessions.filter((session) => !session.archived);
  const archived = sessions.filter((session) => session.archived);
  current.forEach((session) => sessionList.append(sessionRow(session)));
  archived.forEach((session) => archivedList.append(sessionRow(session, true)));
  sessionStatus.textContent = current.length ? "" : "No chats yet.";
  archivedToggle.hidden = archived.length === 0;
  archivedToggle.textContent = `Archived (${archived.length})`;
  archivedToggle.setAttribute("aria-expanded", String(archivedOpen));
  archivedList.hidden = !archivedOpen;
}

async function loadSessions() {
  const response = await fetch("/api/sessions");
  if (!response.ok) throw new Error("sessions unavailable");
  const data = await response.json();
  activeSessionId = data.active_session_id;
  renderSessions(data.sessions || []);
}

async function updateSession(action, sessionId, title = null) {
  if (busy) {
    sessionStatus.textContent = "Wait for current reply to finish.";
    return;
  }
  const wasCurrent = sessionId === activeSessionId;
  sessionStatus.textContent = action === "open" ? "Opening chat…" : "Updating chat…";
  const body = {session_id: sessionId};
  if (title !== null) body.title = title;
  const response = await fetch(`/api/sessions/${action}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "chat update failed");
  activeSessionId = result.active_session_id;
  expandedSessionId = null;
  renamingSessionId = null;
  if (action === "open" || (action === "archive" && wasCurrent)) await loadHistory();
  await loadSessions();
  if (action === "open") {
    setDrawerOpen(false);
    input.focus({preventScroll: true});
  } else {
    sessionStatus.textContent = action === "unarchive" ? "Chat restored." : action === "archive" ? "Chat archived." : "Chat renamed.";
    if (action === "rename") {
      document.querySelector(`.session-more[data-session-id="${CSS.escape(sessionId)}"]`)?.focus();
    } else if (action === "archive") {
      (archivedToggle.hidden ? drawerClose : archivedToggle).focus();
    } else {
      document.querySelector(`.session-open[data-session-id="${CSS.escape(sessionId)}"]`)?.focus();
    }
  }
}

async function startNewChat() {
  if (busy) return;
  drawerNewChat.disabled = true;
  try {
    const response = await fetch("/api/chat/clear", {method: "POST"});
    if (!response.ok) throw new Error("new chat unavailable");
    clearConversation();
    input.value = "";
    resizeComposer();
    updateSendState();
    input.focus({preventScroll: true});
    setStatus("Ready");
    await loadSessions();
  } catch {
    setStatus("Offline");
  } finally {
    drawerNewChat.disabled = false;
  }
}

drawerNewChat.addEventListener("click", () => {
  setDrawerOpen(false);
  startNewChat();
});

menuToggle.addEventListener("click", () => setDrawerOpen(!drawer.classList.contains("open")));

drawerClose.addEventListener("click", () => setDrawerOpen(false));
drawerOverlay.addEventListener("click", () => setDrawerOpen(false));

archivedToggle.addEventListener("click", () => {
  archivedOpen = !archivedOpen;
  archivedToggle.setAttribute("aria-expanded", String(archivedOpen));
  archivedList.hidden = !archivedOpen;
});

drawer.addEventListener("click", (event) => {
  const button = event.target.closest("[data-session-action]");
  if (!(button instanceof HTMLButtonElement)) return;
  const action = button.dataset.sessionAction;
  const sessionId = button.dataset.sessionId;
  if (!action || !sessionId) return;
  if (action === "more") {
    expandedSessionId = expandedSessionId === sessionId ? null : sessionId;
    renamingSessionId = null;
    renderSessions(knownSessions);
    document.querySelector(`.session-item [data-session-id="${CSS.escape(sessionId)}"][data-session-action="${expandedSessionId ? "rename" : "more"}"]`)?.focus();
  } else if (action === "rename") {
    renamingSessionId = sessionId;
    renderSessions(knownSessions);
    document.querySelector(".session-rename input")?.focus();
  } else if (action === "cancel-rename") {
    renamingSessionId = null;
    renderSessions(knownSessions);
    document.querySelector(`.session-more[data-session-id="${CSS.escape(sessionId)}"]`)?.focus();
  } else {
    updateSession(action, sessionId).catch((error) => { sessionStatus.textContent = error.message; });
  }
});

drawer.addEventListener("submit", (event) => {
  const rename = event.target.closest(".session-rename");
  if (!(rename instanceof HTMLFormElement)) return;
  event.preventDefault();
  const title = new FormData(rename).get("title");
  updateSession("rename", rename.dataset.sessionId, String(title || ""))
    .catch((error) => { sessionStatus.textContent = error.message; });
});

document.addEventListener("keydown", (event) => {
  if (!drawer.classList.contains("open")) return;
  if (event.key === "Escape") {
    setDrawerOpen(false);
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...drawer.querySelectorAll("button, input, a[href]")]
    .filter((element) => !element.disabled && !element.closest("[hidden]"));
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

input.addEventListener("input", () => {
  resizeComposer();
  updateSendState();
});

input.addEventListener("focus", () => {
  scheduleViewportSync();
  setTimeout(scheduleViewportSync, 250);
});

input.addEventListener("blur", () => setTimeout(scheduleViewportSync, 100));

input.addEventListener("keydown", (event) => {
  if (window.matchMedia("(pointer: fine)").matches && (event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    form.requestSubmit();
  }
});

resizeComposer();
updateSendState();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || busy) return;

  input.value = "";
  busy = true;
  input.disabled = true;
  updateSendState();
  resizeComposer();
  add({role: "user", content: message});
  setStatus("Thinking");
  let reply = null;
  let streamFinished = false;
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message}),
    });
    if (!response.ok || !response.body) throw new Error("chat unavailable");
    reply = add({role: "assistant", responding: true});
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const handleEvent = (line) => {
      if (!line) return;
      const streamEvent = JSON.parse(line);
      if (streamEvent.type === "model") {
        setModel(streamEvent.content);
        return;
      }
      if (streamEvent.type === "search") {
        setStatus(`Searching: ${streamEvent.content || "web"}...`);
        return;
      }
      if (streamEvent.type === "search_done") {
        setStatus(`Found ${streamEvent.content || "results"}`);
        return;
      }
      if (streamEvent.type === "search_error") {
        setStatus(streamEvent.content || "Search failed");
        return;
      }
      if (streamEvent.type === "delta" && reply) {
        reply.indicator?.remove();
        reply.indicator = null;
        reply.article.classList.remove("typing");
        reply.article.classList.add("streaming");
        reply.text.textContent += streamEvent.content || "";
        scrollToLatest();
      } else if (streamEvent.type === "done") {
        streamFinished = true;
        reply?.article.classList.remove("responding", "typing", "streaming");
        reply?.indicator?.remove();
      } else if (streamEvent.type === "error") {
        streamFinished = true;
        const error = streamEvent.content || "Sage could not complete the response.";
        if (reply) {
          reply.indicator?.remove();
          reply.text.textContent = error;
          reply.article.classList.remove("responding", "typing", "streaming");
          reply.article.classList.add("response-error");
        } else {
          add({role: "assistant", content: error});
        }
      }
    };
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      lines.forEach(handleEvent);
    }
    buffer += decoder.decode();
    handleEvent(buffer);
    if (!streamFinished) throw new Error("incomplete stream");
  } catch {
    const message = "Sage could not complete the response. Your message may have been saved.";
    if (reply) {
      reply.indicator?.remove();
      reply.text.textContent = message;
      reply.article.classList.remove("responding", "typing", "streaming");
      reply.article.classList.add("response-error");
    } else {
      add({role: "assistant", content: message});
    }
  } finally {
    busy = false;
    input.disabled = false;
    updateSendState();
    input.focus({preventScroll: true});
    setStatus("Ready");
  }
});

loadHistory().catch(() => setStatus("Offline")).finally(() => {
  busy = false;
  input.disabled = false;
  updateSendState();
});

window.visualViewport?.addEventListener("resize", scheduleViewportSync);
window.visualViewport?.addEventListener("scroll", scheduleViewportSync);
window.addEventListener("resize", scheduleViewportSync);
window.addEventListener("orientationchange", () => setTimeout(scheduleViewportSync, 100));
syncVisualViewport();
