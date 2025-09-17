#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${RENDER_URL:-}" ]]; then
  echo "RENDER_URL not set" >&2
  exit 1
fi
if [[ -z "${TEST_API_KEY:-}" ]]; then
  echo "TEST_API_KEY not set" >&2
  exit 1
fi

PROMPTS_FILE=${PROMPTS_FILE:-tests/prompts.txt}
if [[ ! -f "$PROMPTS_FILE" ]]; then
  echo "Prompts file not found: $PROMPTS_FILE" >&2
  exit 1
fi

# Defaults; can override via env
BEATS_LIST=${BEATS_LIST:-"1 2 4 5"}
TOKENS_LIST=${TOKENS_LIST:-"800 1200 1600"}

for TOK in $TOKENS_LIST; do
  for B in $BEATS_LIST; do
    echo "=== Running beats=$B tokens=$TOK ==="
    RENDER_URL="$RENDER_URL" TEST_API_KEY="$TEST_API_KEY" \
      python3 tests/live_test.py --prompts-file "$PROMPTS_FILE" --beats "$B" --max-tokens "$TOK"
    echo
    sleep 1
  done
done
