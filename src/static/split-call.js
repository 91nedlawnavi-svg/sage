const button = document.querySelector("#call-button");
const status = document.querySelector("#status");
const timing = document.querySelector("#timing");

const state = {
  recorder: null,
  stream: null,
  chunks: [],
  callId: crypto.randomUUID(),
  generation: 0,
  controller: null,
  audio: null,
  speechBuffer: "",
  slots: new Map(),
  nextSlot: 0,
  nextPlayback: 0,
  playing: false,
  turnStarted: 0,
  sageStarted: 0,
  firstChunkAt: 0,
  sttMs: 0,
  sageMs: 0,
  ttsMs: 0,
  firstAudioMs: 0,
  holding: false,
};

function setStatus(text) {
  status.textContent = text;
}

function stopOutput() {
  state.generation += 1;
  state.controller?.abort();
  state.controller = new AbortController();
  state.audio?.pause();
  state.audio = null;
  state.speechBuffer = "";
  state.slots.clear();
  state.nextSlot = 0;
  state.nextPlayback = 0;
  state.playing = false;
}

function cleanSpeech(text) {
  return text
    .replace(/\[([^\]]+)]\([^)]+\)/g, "$1")
    .replace(/[*_`#]/g, "")
    .trim();
}

function updateTiming() {
  if (!state.turnStarted) return;
  const parts = [`STT ${Math.round(state.sttMs)} ms`];
  if (state.sageMs) parts.push(`Sage sentence ${Math.round(state.sageMs)} ms`);
  if (state.ttsMs) parts.push(`TTS ${Math.round(state.ttsMs)} ms`);
  if (state.firstAudioMs) parts.push(`total ${Math.round(state.firstAudioMs)} ms`);
  timing.textContent = parts.join(" · ");
}

async function synthesize(text, slot, generation) {
  try {
    const response = await fetch("/api/split-voice/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: cleanSpeech(text) }),
      signal: state.controller.signal,
    });
    if (!response.ok) throw new Error("TTS failed");
    const blob = await response.blob();
    if (generation !== state.generation) return;
    state.slots.set(slot, blob);
    playReady(generation);
  } catch (error) {
    if (error.name !== "AbortError") setStatus("TTS failed.");
  }
}

async function playReady(generation) {
  if (state.playing || generation !== state.generation) return;
  const blob = state.slots.get(state.nextPlayback);
  if (!blob) return;
  state.playing = true;
  state.slots.delete(state.nextPlayback++);
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  state.audio = audio;
  audio.onplaying = () => {
    if (!state.firstAudioMs) {
      state.firstAudioMs = performance.now() - state.turnStarted;
      state.ttsMs = performance.now() - state.firstChunkAt;
      updateTiming();
    }
    setStatus("Sage is speaking.");
  };
  const finish = () => {
    URL.revokeObjectURL(url);
    if (state.audio === audio) state.audio = null;
    state.playing = false;
    playReady(generation);
  };
  audio.onended = finish;
  audio.onerror = finish;
  try {
    await audio.play();
  } catch {
    finish();
  }
}

function queueSpeech(text) {
  const clean = cleanSpeech(text);
  if (!clean) return;
  if (!state.firstChunkAt) {
    state.firstChunkAt = performance.now();
    state.sageMs = state.firstChunkAt - state.sageStarted;
    updateTiming();
  }
  const slot = state.nextSlot++;
  synthesize(clean, slot, state.generation);
}

function flushSpeakable(final = false) {
  while (state.speechBuffer) {
    const boundary = state.speechBuffer.match(/^([\s\S]*?[.!?](?:["'’”)]*)?)(?:\s+|$)/);
    if (boundary) {
      queueSpeech(boundary[1]);
      state.speechBuffer = state.speechBuffer.slice(boundary[0].length);
      continue;
    }
    if (state.speechBuffer.length > 600) {
      const cut = state.speechBuffer.lastIndexOf(" ", 600);
      queueSpeech(state.speechBuffer.slice(0, cut > 0 ? cut : 600));
      state.speechBuffer = state.speechBuffer.slice(cut > 0 ? cut : 600).trimStart();
      continue;
    }
    break;
  }
  if (final && state.speechBuffer.trim()) queueSpeech(state.speechBuffer);
  if (final) state.speechBuffer = "";
}

async function streamSage(transcript) {
  const response = await fetch("/api/split-voice/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: transcript,
      call_id: state.callId,
      turn_id: crypto.randomUUID(),
    }),
    signal: state.controller.signal,
  });
  if (!response.ok || !response.body) throw new Error("Sage failed");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let lines = "";
  while (true) {
    const result = await reader.read();
    lines += decoder.decode(result.value || new Uint8Array(), { stream: !result.done });
    const complete = lines.split("\n");
    lines = complete.pop();
    for (const line of complete) {
      if (!line) continue;
      const event = JSON.parse(line);
      if (event.type === "delta") {
        state.speechBuffer += event.content;
        flushSpeakable();
      } else if (event.type === "error") {
        throw new Error(event.content || "Sage failed");
      }
    }
    if (result.done) break;
  }
  flushSpeakable(true);
}

async function processRecording(blob) {
  state.turnStarted = performance.now();
  state.sageStarted = 0;
  state.firstChunkAt = 0;
  state.sttMs = 0;
  state.sageMs = 0;
  state.ttsMs = 0;
  state.firstAudioMs = 0;
  timing.textContent = "";
  try {
    setStatus("Transcribing…");
    const response = await fetch("/api/split-voice/stt", {
      method: "POST",
      headers: { "Content-Type": blob.type || "audio/webm" },
      body: blob,
      signal: state.controller.signal,
    });
    if (!response.ok) throw new Error("STT failed");
    const result = await response.json();
    state.sttMs = performance.now() - state.turnStarted;
    updateTiming();
    if (!result.transcript?.trim()) {
      setStatus("I didn't catch that. Hold to try again.");
      return;
    }
    setStatus("Sage is thinking…");
    state.sageStarted = performance.now();
    await streamSage(result.transcript.trim());
    if (!state.playing && !state.slots.size) setStatus("Hold to talk.");
  } catch (error) {
    if (error.name !== "AbortError") setStatus(error.message || "Voice turn failed.");
  }
}

async function startRecording(event) {
  event.preventDefault();
  if (state.recorder?.state === "recording") return;
  state.holding = true;
  if (event.pointerId !== undefined) button.setPointerCapture(event.pointerId);
  stopOutput();
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (!state.holding) {
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
      setStatus("Hold to talk.");
      return;
    }
    state.chunks = [];
    state.recorder = new MediaRecorder(state.stream);
    state.recorder.ondataavailable = (chunk) => {
      if (chunk.data.size) state.chunks.push(chunk.data);
    };
    state.recorder.onstop = () => {
      const blob = new Blob(state.chunks, { type: state.recorder.mimeType || "audio/webm" });
      state.stream.getTracks().forEach((track) => track.stop());
      state.stream = null;
      button.classList.remove("active");
      if (blob.size > 500) processRecording(blob);
      else setStatus("Hold to talk.");
    };
    state.recorder.start();
    button.classList.add("active");
    setStatus("Listening… release when done.");
  } catch {
    setStatus("Microphone unavailable.");
  }
}

function stopRecording(event) {
  event?.preventDefault();
  state.holding = false;
  if (state.recorder?.state === "recording") state.recorder.stop();
}

button.addEventListener("pointerdown", startRecording);
button.addEventListener("pointerup", stopRecording);
button.addEventListener("pointercancel", stopRecording);
button.addEventListener("keydown", (event) => {
  if ((event.key === " " || event.key === "Enter") && !event.repeat) startRecording(event);
});
button.addEventListener("keyup", (event) => {
  if (event.key === " " || event.key === "Enter") stopRecording(event);
});
window.addEventListener("beforeunload", () => {
  stopOutput();
  state.stream?.getTracks().forEach((track) => track.stop());
});
