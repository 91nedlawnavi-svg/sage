const messages = document.querySelector("#messages");
const empty = document.querySelector("#empty");
const form = document.querySelector("#composer");
const input = document.querySelector("#message");
const status = document.querySelector("#status");
const statusDot = document.querySelector("#status-dot");
const send = document.querySelector("#send-btn");
const notebookToggle = document.querySelector("#notebook-toggle");
const drawer = document.querySelector("#drawer");
const drawerClose = document.querySelector("#drawer-close");
const drawerOverlay = document.querySelector("#drawer-overlay");
const drawerTabs = document.querySelectorAll(".drawer-tab");
const drawerContent = document.querySelector("#drawer-content");

let activeTab = "reflections";

input.disabled = true;
send.disabled = true;

function setDrawerOpen(open) {
  drawer.classList.toggle("open", open);
  document.body.classList.toggle("drawer-open", open);
}

function resizeComposer() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

let heldCloseMode = false;
  const pvNotice = document.querySelector("#pv-notice");

function add(event) {
  if (empty) empty.style.display = "none";
  const article = document.createElement("article");
  article.className = event.role;
  if (event.kind === "waiting") {
    article.classList.add("waiting");
  }
  const label = document.createElement("strong");
  label.textContent = event.role === "user" ? "You" : (event.kind === "waiting" ? "Sage (Waiting)" : "Sage");
  const text = document.createElement("p");
  text.textContent = event.content || "";
  article.append(label, text);
  messages.append(article);
  article.scrollIntoView({block: "end"});
  return {article, text};
}

function updatePvMode() {
  heldCloseMode = !heldCloseMode;
  pvNotice.hidden = !heldCloseMode;
}

function setHeldClose(article, control, heldClose) {
  article.classList.toggle("held-close", heldClose);
  control.textContent = heldClose ? "Open" : "Hold close";
  control.setAttribute("aria-pressed", String(heldClose));
}

async function loadHistory() {
  const response = await fetch("/api/history");
  if (!response.ok) throw new Error("history unavailable");
  const {events} = await response.json();
  if (events && events.length > 0) {
    if (empty) empty.style.display = "none";
    for (const event of events) add(event);
  }
  status.textContent = "ready";
  if (statusDot) statusDot.classList.remove("cold");
}

notebookToggle.addEventListener("click", () => {
  const open = !drawer.classList.contains("open");
  setDrawerOpen(open);
  if (open) loadDrawerTab(activeTab);
});

drawerClose.addEventListener("click", () => setDrawerOpen(false));
drawerOverlay.addEventListener("click", () => setDrawerOpen(false));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && drawer.classList.contains("open")) setDrawerOpen(false);
});

input.addEventListener("input", resizeComposer);
resizeComposer();

input.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    form.requestSubmit();
  }
});

drawerTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    drawerTabs.forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    activeTab = tab.dataset.tab;
    loadDrawerTab(activeTab);
  });
});

async function loadDrawerTab(tab) {
  drawerContent.innerHTML = "Loading...";
  try {
    if (tab === "reflections") {
      const res = await fetch("/api/reflections");
      const data = await res.json();
      const list = data.reflections || [];
      if (list.length === 0) {
        drawerContent.innerHTML = "<div style='color:var(--muted);'>No reflections recorded yet.</div>";
        return;
      }
      drawerContent.innerHTML = list.slice().reverse().map(r => `
        <div class="notebook-card">
          <div class="notebook-card-ts">${new Date(r.said_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</div>
          <div>${escapeHtml(r.content)}</div>
        </div>
      `).join("");
    } else if (tab === "beliefs") {
      const res = await fetch("/api/beliefs");
      const data = await res.json();
      const list = data.beliefs || [];
      if (list.length === 0) {
        drawerContent.innerHTML = "<div style='color:var(--muted);'>No beliefs recorded yet.</div>";
        return;
      }
      drawerContent.innerHTML = list.slice().reverse().map(b => `
        <div class="notebook-card">
          <strong>${escapeHtml(b.topic)}</strong>
          <div>${escapeHtml(b.stance)}</div>
          <div class="notebook-card-ts" style="margin-top:4px;">Evidence: ${escapeHtml(b.evidence)}</div>
        </div>
      `).join("");
    } else if (tab === "entities") {
      const res = await fetch("/api/entities");
      const data = await res.json();
      const list = data.entities || [];
      if (list.length === 0) {
        drawerContent.innerHTML = "<div style='color:var(--muted);'>No entities observed yet.</div>";
        return;
      }
      drawerContent.innerHTML = list.slice().reverse().map(e => `
        <div class="notebook-card">
          <strong>${escapeHtml(e.name)}</strong>
          <div>${escapeHtml(e.observation)}</div>
        </div>
      `).join("");
    }
  } catch {
    drawerContent.innerHTML = "<div style='color:var(--muted);'>Failed to load data.</div>";
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;

  // Handle /pv command (ephemeral mode toggle)
  if (message === "/pv") {
    updatePvMode();
    input.value = "";
    resizeComposer();
    return;
  }

  input.value = "";
  input.disabled = true;
  send.disabled = true;
  add({role: "user", content: message});
  status.textContent = "thinking";
  let reply = null;
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message, held_close_mode: heldCloseMode}),
    });
    if (!response.ok || !response.body) throw new Error("chat unavailable");
    const heldClose = response.headers.get("X-Sage-Held-Close") === "true";
    if (!heldClose) reply = add({role: "assistant"}).text;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      if (reply) {
        reply.textContent += decoder.decode(value, {stream: true});
        reply.parentElement.scrollIntoView({block: "end"});
      }
    }
    if (reply) reply.textContent += decoder.decode();
  } catch {
    if (reply) {
      reply.textContent = "Sage could not reach the local server. Your message may have been saved.";
    } else {
      add({role: "assistant", content: "Sage could not reach the local server. Your message may have been saved."});
    }
  } finally {
    input.disabled = false;
    send.disabled = false;
    input.focus();
    status.textContent = "ready";
  }
});

loadHistory().catch(() => { status.textContent = "offline"; }).finally(() => {
  input.disabled = false;
  send.disabled = false;
  input.focus();
});
