const messages = document.querySelector("#messages");
const form = document.querySelector("#composer");
const input = document.querySelector("#message");
const status = document.querySelector("#status");

function add(role, content = "") {
  const article = document.createElement("article");
  article.className = role;
  const label = document.createElement("strong");
  label.textContent = role === "user" ? "You" : "Sage";
  const text = document.createElement("p");
  text.textContent = content;
  article.append(label, text);
  messages.append(article);
  article.scrollIntoView({block: "end"});
  return text;
}

async function loadHistory() {
  const response = await fetch("/api/history");
  if (!response.ok) throw new Error("history unavailable");
  const {events} = await response.json();
  for (const event of events) add(event.role, event.content);
  status.textContent = "ready";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  input.disabled = true;
  form.querySelector("button").disabled = true;
  add("user", message);
  const reply = add("assistant");
  status.textContent = "thinking";
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message}),
    });
    if (!response.ok || !response.body) throw new Error("chat unavailable");
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
    form.querySelector("button").disabled = false;
    input.focus();
    status.textContent = "ready";
  }
});

loadHistory().catch(() => { status.textContent = "offline"; });
