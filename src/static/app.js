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
const drawerTabs = document.querySelectorAll(".drawer-tab");
const drawerContent = document.querySelector("#drawer-content");

let activeTab = "reflections";

input.disabled = true;
send.disabled = true;

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
  if (event.role === "user" && event.id) {
    addPrivacyControl(article, event);
  }
  messages.append(article);
  article.scrollIntoView({block: "end"});
  return {article, text};
}

function addPrivacyControl(article, event) {
  const control = document.createElement("button");
  control.type = "button";
  control.className = "privacy";
  control.dataset.eventId = event.id;
  setHeldClose(article, control, event.held_close);
  control.addEventListener("click", async () => {
    control.disabled = true;
    try {
      const response = await fetch(`/api/events/${encodeURIComponent(event.id)}/privacy`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({held_close: !article.classList.contains("held-close")}),
      });
      if (!response.ok) throw new Error("privacy unavailable");
      const result = await response.json();
      setHeldClose(article, control, result.held_close);
    } catch {
      status.textContent = "privacy setting unavailable";
    } finally {
      control.disabled = false;
    }
  });
  article.append(control);
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
  drawer.classList.toggle("open");
  if (drawer.classList.contains("open")) {
    loadDrawerTab(activeTab);
  }
});

drawerClose.addEventListener("click", () => {
  drawer.classList.remove("open");
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
  input.value = "";
  input.disabled = true;
  send.disabled = true;
  const user = add({role: "user", content: message});
  const reply = add({role: "assistant"}).text;
  status.textContent = "thinking";
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message}),
    });
    if (!response.ok || !response.body) throw new Error("chat unavailable");
    const eventId = response.headers.get("X-Sage-Event-ID");
    if (eventId) addPrivacyControl(user.article, {
      id: eventId,
      held_close: response.headers.get("X-Sage-Held-Close") === "true",
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      reply.textContent += decoder.decode(value, {stream: true});
      reply.parentElement.scrollIntoView({block: "end"});
    }
    reply.textContent += decoder.decode();
  } catch {
    reply.textContent = "Sage could not reach the local server. Your message may have been saved.";
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
