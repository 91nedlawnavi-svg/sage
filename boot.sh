#!/bin/bash
# boot.sh — Start Sage (local memory + embeddings, NVIDIA NIM for chat)
# Run from any directory: bash /path/to/sage/boot.sh

set -e

SAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$HOME/sage_logs"
DATA_DIR="$HOME/sage_data"

mkdir -p "$LOG_DIR" "$DATA_DIR"

# Copy default directive on first run
if [ ! -f "$DATA_DIR/directive.txt" ]; then
  cp "$SAGE_DIR/directive.txt" "$DATA_DIR/directive.txt"
  echo "[boot] directive.txt copied to $DATA_DIR"
fi

# Verify NVIDIA API key is set
if [ -z "$NVIDIA_API_KEY" ]; then
  echo "[boot] ⚠️  NVIDIA_API_KEY is not set. Chat will not work."
  echo "[boot]     Run: export NVIDIA_API_KEY=nvapi-xxxx"
  exit 1
fi

echo "[boot] Sage starting..."
echo "[boot] Logs → $LOG_DIR"
echo "[boot] Chat → NVIDIA NIM (Llama 4 Maverick)"

# ── MEMORY WRITER (Port 8081) ─────────────────────────────────────────
# Qwen 2.5 7B — CPU only, low temperature, reflection + distillation
"$HOME/llama.cpp/build/bin/llama-server" \
  -m "$HOME/models/qwen7b/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf" \
  --port 8081 \
  --n-gpu-layers 0 \
  --threads 4 \
  --threads-batch 4 \
  --ctx-size 2048 \
  --temp 0.1 \
  > "$LOG_DIR/memory.log" 2>&1 &
echo "[boot] Memory writer PID $! (port 8081)"

# ── EMBEDDINGS (Port 8082) ────────────────────────────────────────────
# BGE-M3 — partial GPU offload, multilingual semantic retrieval
"$HOME/llama.cpp/build/bin/llama-server" \
  -m "$HOME/models/bge-m3/bge-m3-Q4_K_M.gguf" \
  --port 8082 \
  --n-gpu-layers 99 \
  --ctx-size 8192 \
  --pooling cls \
  --embedding \
  > "$LOG_DIR/embed.log" 2>&1 &
echo "[boot] Embeddings PID $! (port 8082)"

# ── WAIT FOR MODELS ───────────────────────────────────────────────────
echo "[boot] Waiting 20s for local models to load..."
sleep 20

# ── PYTHON UI (Port 6969) ─────────────────────────────────────────────
cd "$SAGE_DIR"
python launch.py > "$LOG_DIR/ui.log" 2>&1 &
echo "[boot] UI PID $! (port 6969)"

echo "[boot] ─────────────────────────────────"
echo "[boot] Sage is live → http://localhost:6969"
echo "[boot] Logs → $LOG_DIR/"
