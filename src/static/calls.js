const callsRoot = document.querySelector("#calls");
const status = document.querySelector("#status");
const wib = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Jakarta",
  dateStyle: "medium",
  timeStyle: "short",
});

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function timeLabel(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : `${wib.format(date)} WIB`;
}

function correctionForm(event, transcript) {
  const form = element("form", "edit-form");
  form.hidden = true;
  const field = element("textarea");
  field.name = "content";
  field.value = event.content;
  field.maxLength = 1500;
  field.required = true;
  field.setAttribute("aria-label", `Correct ${event.role} transcript`);
  const actions = element("div", "actions");
  const save = element("button", "primary", "Save correction");
  save.type = "submit";
  const cancel = element("button", "", "Cancel");
  cancel.type = "button";
  actions.append(save, cancel);
  form.append(field, actions);

  cancel.addEventListener("click", () => {
    form.hidden = true;
    transcript.querySelector(".edit").focus();
  });
  form.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    save.disabled = true;
    try {
      const response = await fetch("/api/transcript-corrections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_id: event.id, content: field.value }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Correction could not be saved.");
      await loadCalls();
    } catch (error) {
      status.textContent = error.message;
      save.disabled = false;
    }
  });
  return form;
}

function renderTranscript(event) {
  const transcript = element("section", "transcript");
  transcript.append(
    element("p", "speaker", event.role === "user" ? "You" : "Sage"),
    element("p", "words", event.content),
  );
  if (event.original_content !== undefined) {
    const original = element("details");
    original.append(element("summary", "", "Original transcript"), element("p", "", event.original_content));
    transcript.append(original);
  }
  const actions = element("div", "actions");
  const edit = element("button", "edit", "Correct transcript");
  edit.type = "button";
  actions.append(edit);
  if (event.original_content !== undefined) actions.append(element("span", "saved", "Corrected"));
  transcript.append(actions);
  const form = correctionForm(event, transcript);
  transcript.append(form);
  edit.addEventListener("click", () => {
    form.hidden = false;
    form.querySelector("textarea").focus();
  });
  return transcript;
}

function renderCall(call, index) {
  const section = element("section", "call");
  const heading = element("h2", "call-heading", `Call ${timeLabel(call.said_at)}`);
  heading.id = `call-${index}`;
  section.setAttribute("aria-labelledby", heading.id);
  section.append(heading);
  for (const turn of call.turns) {
    const article = element("article", "turn");
    article.append(element("p", "turn-time", timeLabel(turn.said_at)));
    for (const event of turn.events) article.append(renderTranscript(event));
    section.append(article);
  }
  return section;
}

async function loadCalls() {
  status.textContent = "Loading calls…";
  try {
    const response = await fetch("/api/calls");
    const result = await response.json();
    if (!response.ok) throw new Error("Calls could not be loaded.");
    callsRoot.replaceChildren(...result.calls.map(renderCall));
    status.textContent = result.calls.length ? "" : "No reviewable calls yet. New calls will appear here.";
  } catch (error) {
    status.textContent = error.message;
  }
}

loadCalls();
