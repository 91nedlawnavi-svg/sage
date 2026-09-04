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
const sensitiveNotice = document.querySelector("#sensitive-notice");

let sensitiveMode = false;
let busy = true;
let focusBeforeDrawer = null;
let viewportFrame = 0;

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
  article.classList.toggle("sensitive", event.sensitive === true);
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

function toggleSensitiveMode() {
  sensitiveMode = !sensitiveMode;
  sensitiveNotice.hidden = !sensitiveMode;
}

async function loadHistory() {
  const response = await fetch("/api/history");
  if (!response.ok) throw new Error("history unavailable");
  const {events, model: alias} = await response.json();
  setModel(alias);
  for (const event of events || []) add(event);
  setStatus("Ready");
}

async function startNewChat() {
  if (busy) return;
  drawerNewChat.disabled = true;
  try {
    const response = await fetch("/api/chat/clear", {method: "POST"});
    if (!response.ok) throw new Error("new chat unavailable");
    messages.querySelectorAll("article").forEach((article) => article.remove());
    empty.hidden = false;
    sensitiveMode = false;
    sensitiveNotice.hidden = true;
    input.value = "";
    resizeComposer();
    updateSendState();
    input.focus({preventScroll: true});
    setStatus("Ready");
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

document.addEventListener("keydown", (event) => {
  if (!drawer.classList.contains("open")) return;
  if (event.key === "Escape") {
    setDrawerOpen(false);
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...drawer.querySelectorAll("button, a[href]")];
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

  if (message === "/sensitive") {
    toggleSensitiveMode();
    input.value = "";
    resizeComposer();
    updateSendState();
    return;
  }

  input.value = "";
  busy = true;
  input.disabled = true;
  updateSendState();
  resizeComposer();
  add({role: "user", content: message, sensitive: sensitiveMode});
  setStatus("Thinking");
  let reply = null;
  let streamFinished = false;
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message, sensitive_mode: sensitiveMode}),
    });
    if (!response.ok || !response.body) throw new Error("chat unavailable");
    const sensitive = response.headers.get("X-Sage-Sensitive") === "true";
    if (!sensitive) reply = add({role: "assistant", responding: true});
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
