#!/usr/bin/env bash
set -euo pipefail

# --- Locate project root (directory of this script) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "👉 Project: $SCRIPT_DIR"

# --- Python venv setup ---
if [[ ! -d "venv" ]]; then
  echo "🐍 Creating virtual environment (venv)…"
  python3 -m venv venv
fi

# shellcheck disable=SC1091
source "venv/bin/activate"

# --- Requirements ---
if [[ -f "requirements.txt" ]]; then
  echo "📦 Installing requirements…"
  pip install -r requirements.txt >/dev/null
else
  echo "⚠️  requirements.txt not found; skipping pip install."
fi

# --- .env support for XAI_API_KEY ---
ENV_FILE=".env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

# If still missing, prompt once and persist to .env
if [[ -z "${XAI_API_KEY:-}" ]]; then
  read -r -p "🔑 Enter your XAI_API_KEY (input hidden): " -s KEY
  echo
  if [[ -z "$KEY" ]]; then
    echo "❌ No key entered. Aborting."
    exit 1
  fi
  export XAI_API_KEY="$KEY"
  # Persist for next time
  {
    echo ""
    echo "# Added by start_grok.sh on $(date)"
    echo "XAI_API_KEY=\"$XAI_API_KEY\""
  } >> "$ENV_FILE"
  echo "✅ Saved XAI_API_KEY to .env"
else
  export XAI_API_KEY  # ensure exported for this shell
fi

# --- ElevenLabs TTS setup ---
if [[ -z "${ELEVENLABS_API_KEY:-}" ]]; then
  read -r -p "🎤 Enter your ELEVENLABS_API_KEY (optional, press Enter to skip): " -s TTS_KEY
  echo
  if [[ -n "$TTS_KEY" ]]; then
    export ELEVENLABS_API_KEY="$TTS_KEY"
    # Persist for next time
    {
      echo ""
      echo "# Added by start_grok.sh on $(date)"
      echo "ELEVENLABS_API_KEY=\"$ELEVENLABS_API_KEY\""
    } >> "$ENV_FILE"
    echo "✅ Saved ELEVENLABS_API_KEY to .env"
  else
    echo "⏭️  Skipping ElevenLabs setup"
  fi
else
  export ELEVENLABS_API_KEY  # ensure exported for this shell
fi

# --- Optional: create a starter opener if missing ---
if [[ ! -f "opener.txt" ]]; then
  cat > opener.txt <<'TXT'
Stephanie, a 56-year-old elementary reading teacher, sat across from Principal Dan in his office after hours. The door clicked locked; blinds drew shut; the building’s hum filled the quiet. She adjusted the hem of her skirt as his voice dropped and he stepped closer, sleeves rolled, cologne warm in the stale air.
TXT
  echo "📝 Created starter opener.txt"
fi

# --- Run the app ---
echo "🚀 Launching chat.py (Ctrl+C to exit)…"
python3 chat.py
