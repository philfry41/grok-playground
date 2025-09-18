#!/usr/bin/env bash
set -euo pipefail

RENDER_URL="${RENDER_URL:-http://localhost:5000}"
TEST_API_KEY="${TEST_API_KEY:-}"
INSTR="${1:-make her scream his name}"

hdrs=( -H "Content-Type: application/json" )
if [[ -n "${TEST_API_KEY}" ]]; then
  hdrs+=( -H "Authorization: Bearer ${TEST_API_KEY}" -H "X-Test-Api-Key: ${TEST_API_KEY}" )
fi

echo "== OOC Preview: ${INSTR} =="
curl -sS -X POST "${RENDER_URL}/api/chat" \
  "${hdrs[@]}" \
  -d "{\"message\":\"/ooc rewrite ${INSTR}\",\"beats\":3}" | jq . || true

echo
echo "== OOC Apply =="
curl -sS -X POST "${RENDER_URL}/api/chat" \
  "${hdrs[@]}" \
  -d '{"message":"/ooc apply"}' | jq . || true

echo
echo "Done."


