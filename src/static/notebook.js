const tabs = [...document.querySelectorAll(".notebook-tab")];
const content = document.querySelector("#notebook-content");

const WIB_TIME_FORMAT = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Asia/Jakarta",
});

function formatWib(timestamp) {
  return `${WIB_TIME_FORMAT.format(new Date(timestamp))} WIB`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

function setState(message) {
  content.innerHTML = `<div class="notebook-state">${message}</div>`;
}

function selectTab(selected, focus = false) {
  for (const tab of tabs) {
    const active = tab === selected;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  }
  if (focus) selected.focus();
  loadTab(selected.dataset.tab);
}

async function loadTab(tab) {
  setState("Loading…");
  try {
    const endpoint = tab === "reflections" ? "/api/reflections" : tab === "beliefs" ? "/api/beliefs" : tab === "identity" ? "/api/identity" : "/api/entities";
    const response = await fetch(endpoint);
    if (!response.ok) throw new Error("notebook unavailable");
    const data = await response.json();
    const list = data[tab] || [];
    if (list.length === 0) {
      setState(`No ${tab} recorded yet.`);
      return;
    }
    if (tab === "reflections") {
      content.innerHTML = list.slice().reverse().map((reflection) => `
        <div class="notebook-card">
          <div class="notebook-card-ts">${formatWib(reflection.said_at)}</div>
          <div>${escapeHtml(reflection.content)}</div>
        </div>
      `).join("");
    } else if (tab === "beliefs") {
      content.innerHTML = list.slice().reverse().map((belief) => `
        <div class="notebook-card">
          <strong>${escapeHtml(belief.topic)}</strong>
          <div>${escapeHtml(belief.stance)}</div>
          <div class="notebook-evidence">Evidence: ${escapeHtml(belief.evidence)}</div>
        </div>
      `).join("");
    } else if (tab === "identity") {
      content.innerHTML = list.map((entry) => `
        <div class="notebook-card identity-card">
          <div class="identity-status identity-${entry.status}">${escapeHtml(entry.status)}</div>
          <div>${escapeHtml(entry.claim)}</div>
          ${entry.status === "proposed" ? `
            <div class="identity-actions">
              <button class="identity-btn ratify" data-identity-id="${entry.id}" data-action="ratify">Ratify</button>
              <button class="identity-btn reject" data-identity-id="${entry.id}" data-action="reject">Reject</button>
            </div>
          ` : ""}
        </div>
      `).join("");
    } else {
      content.innerHTML = list.slice().reverse().map((entity) => `
        <div class="notebook-card">
          <strong>${escapeHtml(entity.name)}</strong>
          <div>${escapeHtml(entity.observation)}</div>
        </div>
      `).join("");
    }
  } catch {
    setState("Notebook data could not be loaded.");
  }
}

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === "ArrowRight" ? 1 : -1;
    selectTab(tabs[(index + direction + tabs.length) % tabs.length], true);
  });
});

content.addEventListener("click", async (event) => {
  const btn = event.target.closest("[data-identity-id]");
  if (!btn) return;
  btn.disabled = true;
  try {
    const response = await fetch(`/api/identity/${encodeURIComponent(btn.dataset.identityId)}/${btn.dataset.action}`, {method: "POST"});
    if (!response.ok) throw new Error("ruling failed");
    loadTab("identity");
  } catch {
    btn.disabled = false;
  }
});

loadTab("reflections");
