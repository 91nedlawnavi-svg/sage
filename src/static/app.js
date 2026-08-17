const messages = document.querySelector("#messages");
const form = document.querySelector("#composer");
const input = document.querySelector("#message");
const status = document.querySelector("#status");
const send = form.querySelector("button");
input.disabled = true;
send.disabled = true;

function add(event) {
  const article = document.createElement("article");
  article.className = event.role;
  const label = document.createElement("strong");
  label.textContent = event.role === "user" ? "You" : "Sage";
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
  for (const event of events) add(event);
  status.textContent = "ready";
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
