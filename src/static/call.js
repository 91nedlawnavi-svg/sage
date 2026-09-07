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

  const content = message.serverContent;
  if (!content) return;
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
  if (content.turnComplete) setStatus("Listening.");
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
