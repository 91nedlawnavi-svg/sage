const button = document.querySelector("#call-button");
const status = document.querySelector("#status");

const state = {
  socket: null,
  stream: null,
  inputContext: null,
  outputContext: null,
  capture: null,
  playback: null,
  silentGain: null,
  connectTimer: null,
  saveTimer: null,
  callId: null,
  deletionGeneration: null,
  userTranscript: "",
  assistantTranscript: "",
  turnClosing: false,
  stopping: false,
};

function setStatus(message) {
  status.textContent = message;
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

function floatToPcm16(samples) {
  const pcm = new Int16Array(samples.length);
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    pcm[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return pcm.buffer;
}

function base64ToFloat(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  const pcm = new Int16Array(bytes.buffer);
  const samples = new Float32Array(pcm.length);
  for (let index = 0; index < pcm.length; index += 1) {
    samples[index] = pcm[index] / 32768;
  }
  return samples;
}

async function prepareAudio() {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext || !navigator.mediaDevices?.getUserMedia) {
    throw new Error("Open Sage on localhost or HTTPS to use the microphone.");
  }

  state.inputContext = new AudioContext({ sampleRate: 16000 });
  state.outputContext = new AudioContext({ sampleRate: 24000 });
  await Promise.all([
    state.inputContext.audioWorklet.addModule("/static/capture.worklet.js"),
    state.outputContext.audioWorklet.addModule("/static/playback.worklet.js"),
    state.inputContext.resume(),
    state.outputContext.resume(),
  ]);

  state.stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  const source = state.inputContext.createMediaStreamSource(state.stream);
  state.capture = new AudioWorkletNode(state.inputContext, "sage-capture");
  state.silentGain = state.inputContext.createGain();
  state.silentGain.gain.value = 0;
  source.connect(state.capture).connect(state.silentGain).connect(state.inputContext.destination);

  state.playback = new AudioWorkletNode(state.outputContext, "sage-playback");
  state.playback.connect(state.outputContext.destination);
}

function sendAudio(samples) {
  if (state.socket?.readyState !== WebSocket.OPEN) return;
  state.socket.send(JSON.stringify({
    realtimeInput: {
      audio: {
        data: arrayBufferToBase64(floatToPcm16(samples)),
        mimeType: "audio/pcm;rate=16000",
      },
    },
  }));
}

function mergeTranscript(current, update) {
  const clean = update.trim();
  if (!clean) return current;
  if (!current || clean.startsWith(current)) return clean;
  if (current.endsWith(clean)) return current;
  const separator = /\s$/.test(current) || /^[\s,.;:!?]/.test(update) ? "" : " ";
  return `${current}${separator}${update}`.trim();
}

function recordTranscript(role, text) {
  const key = role === "user" ? "userTranscript" : "assistantTranscript";
  state[key] = mergeTranscript(state[key], text);
  if (state.turnClosing) scheduleTurnSave();
}

function scheduleTurnSave() {
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => {
    state.saveTimer = null;
    saveTurn();
  }, 600);
}

async function saveTurn(keepalive = false) {
  const user = state.userTranscript;
  const assistant = state.assistantTranscript;
  const callId = state.callId;
  state.userTranscript = "";
  state.assistantTranscript = "";
  state.turnClosing = false;
  if ((!user && !assistant) || !callId) return;

  try {
    const response = await fetch("/api/live-turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user, assistant, call_id: callId, deletion_generation: state.deletionGeneration }),
      keepalive,
    });
    if (response.status === 409) {
      setStatus("Memory changed. End this call and start a new one; this turn was not saved.");
      return;
    }
    if (!response.ok) throw new Error();
  } catch {
    if (state.socket) setStatus("Call active; this turn was not saved.");
  }
}

async function handleToolCall(toolCall) {
  setStatus("Remembering…");
  const functionResponses = await Promise.all((toolCall.functionCalls || []).map(async (call) => {
    if (call.name !== "recall_memory") {
      return { name: call.name, id: call.id, response: { error: "Unknown Sage tool." } };
    }
    const query = typeof call.args?.query === "string" ? call.args.query : "";
    try {
      const response = await fetch("/api/live-memory", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error();
      return { name: call.name, id: call.id, response: { result: result.events } };
    } catch {
      return { name: call.name, id: call.id, response: { error: "Sage memory is unavailable." } };
    }
  }));
  if (state.socket?.readyState === WebSocket.OPEN && functionResponses.length) {
    state.socket.send(JSON.stringify({ toolResponse: { functionResponses } }));
  }
}

async function handleMessage(event) {
  const text = event.data instanceof Blob ? await event.data.text() : event.data;
  const message = JSON.parse(text);
  if (message.setupComplete) {
    clearTimeout(state.connectTimer);
    state.connectTimer = null;
    state.capture.port.onmessage = (captureEvent) => sendAudio(captureEvent.data);
    setStatus("Listening.");
    button.disabled = false;
    return;
  }

  if (message.toolCall) {
    await handleToolCall(message.toolCall);
    return;
  }

  const content = message.serverContent;
  if (!content) return;
  if (content.inputTranscription?.text) {
    recordTranscript("user", content.inputTranscription.text);
  }
  if (content.outputTranscription?.text) {
    recordTranscript("assistant", content.outputTranscription.text);
  }
  if (content.interrupted) {
    state.playback?.port.postMessage("clear");
    setStatus("Listening.");
  }
  for (const part of content.modelTurn?.parts || []) {
    if (part.inlineData?.data) {
      state.playback?.port.postMessage(base64ToFloat(part.inlineData.data));
      setStatus("Sage is speaking.");
    }
  }
  if (content.turnComplete) {
    state.turnClosing = true;
    scheduleTurnSave();
    setStatus("Listening.");
  }
}

async function startCall() {
  button.disabled = true;
  setStatus("Preparing your microphone…");
  try {
    await prepareAudio();
    setStatus("Connecting…");
    const response = await fetch("/api/live-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const config = await response.json();
    if (!response.ok) throw new Error(config.error || "The call could not start.");
    state.callId = config.call_id;
    state.deletionGeneration = config.deletion_generation;

    const endpoint = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained";
    state.socket = new WebSocket(`${endpoint}?access_token=${encodeURIComponent(config.token)}`);
    state.connectTimer = setTimeout(() => stopCall("Gemini Live took too long to connect."), 15000);
    state.socket.onopen = () => {
      state.socket.send(JSON.stringify({
        setup: {
          model: config.model,
        },
      }));
    };
    state.socket.onmessage = (event) => handleMessage(event).catch(() => stopCall("The call lost an audio message."));
    state.socket.onerror = () => stopCall("Gemini Live could not connect.");
    state.socket.onclose = () => {
      if (!state.stopping) stopCall("Call ended.");
    };
    button.textContent = "End call";
    button.setAttribute("aria-pressed", "true");
    button.classList.add("active");
  } catch (error) {
    stopCall(error.message || "The call could not start.");
  }
}

function stopCall(message = "Ready when you are.") {
  state.stopping = true;
  clearTimeout(state.connectTimer);
  clearTimeout(state.saveTimer);
  state.saveTimer = null;
  saveTurn(true);
  if (state.socket?.readyState === WebSocket.OPEN) {
    state.socket.send(JSON.stringify({ realtimeInput: { audioStreamEnd: true } }));
  }
  if (state.socket) state.socket.onclose = null;
  state.socket?.close();
  state.stream?.getTracks().forEach((track) => track.stop());
  state.capture?.disconnect();
  state.silentGain?.disconnect();
  state.playback?.disconnect();
  state.inputContext?.close();
  state.outputContext?.close();
  Object.assign(state, {
    socket: null,
    stream: null,
    inputContext: null,
    outputContext: null,
    capture: null,
    playback: null,
    silentGain: null,
    connectTimer: null,
    saveTimer: null,
    callId: null,
    userTranscript: "",
    assistantTranscript: "",
    turnClosing: false,
  });
  button.textContent = "Start call";
  button.setAttribute("aria-pressed", "false");
  button.classList.remove("active");
  button.disabled = false;
  setStatus(message);
  state.stopping = false;
}

button.addEventListener("click", () => {
  if (state.socket || state.stream) stopCall();
  else startCall();
});

window.addEventListener("beforeunload", () => stopCall());
