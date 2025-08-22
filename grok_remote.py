import os, re, requests

API_BASE = "https://api.x.ai/v1"
API_KEY  = os.getenv("XAI_API_KEY")  # set: export XAI_API_KEY=...

THINK_BLOCK_RE = re.compile(
    r"(<think>.*?</think>|<\|begin_of_thought\|>.*?<\|end_of_thought\|>|```(?:thinking|reasoning|cot|cog).*?```)",
    re.IGNORECASE | re.DOTALL,
)
THOUGHT_PREFIX_RE = re.compile(r"^\s*(?:Thought:|Reasoning:)\s*", re.IGNORECASE | re.MULTILINE)

def _clean_thinking(s: str) -> str:
    s = THINK_BLOCK_RE.sub("", s)
    s = THOUGHT_PREFIX_RE.sub("", s)
    return s.strip()

def chat_with_grok(
    messages,
    model="grok-3",
    temperature=1.05,
    max_tokens=1200,
    presence_penalty=0.6,
    frequency_penalty=0.3,
    top_p=0.9,
    hide_thinking=True,
    stop=None,
):
    if not API_KEY:
        raise RuntimeError("Missing XAI_API_KEY environment variable.")
    url = f"{API_BASE}/chat/completions"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model or os.getenv("XAI_MODEL", "grok-3"),
        "messages": messages,
        "temperature": float(temperature),
        "top_p": float(top_p),
        "max_tokens": int(max_tokens),
        "stream": False,
    }
    if presence_penalty is not None:
        payload["presence_penalty"] = float(presence_penalty)
    if frequency_penalty is not None:
        payload["frequency_penalty"] = float(frequency_penalty)
    if stop: payload["stop"] = stop

    r = requests.post(url, headers=headers, json=payload, timeout=30)
    try:
        r.raise_for_status()
    except requests.HTTPError as http_err:
        # Surface server-provided error details to the caller for easier debugging
        status_code = r.status_code
        detail: str | dict | None
        try:
            detail_json = r.json()
            # Common shapes: {"error": {...}} or direct message
            detail = detail_json.get("error", detail_json)
        except Exception:
            detail = r.text

        # Optional debug logging to stdout when XAI_DEBUG is set
        if os.getenv("XAI_DEBUG"):
            # Avoid printing secrets
            safe_payload = {k: v for k, v in payload.items() if k not in {"messages"}}
            print(f"[xai-debug] status={status_code} model={safe_payload.get('model')} payload_keys={list(safe_payload.keys())}")
            print(f"[xai-debug] server_error={detail}")

        # Retry once with a minimal payload if 400 Bad Request (likely invalid params)
        if status_code == 400 and os.getenv("XAI_RETRY_MINIMAL", "1") != "0":
            minimal_payload = {
                "model": payload["model"],
                "messages": payload["messages"],
                "max_tokens": min(512, int(payload.get("max_tokens", 512))),
                "stream": False,
            }
            r2 = requests.post(url, headers=headers, json=minimal_payload, timeout=30)
            try:
                r2.raise_for_status()
            except requests.HTTPError as http_err2:
                # Include both errors
                second_detail: str | dict | None
                try:
                    jd = r2.json()
                    second_detail = jd.get("error", jd)
                except Exception:
                    second_detail = r2.text
                raise requests.HTTPError(
                    f"{http_err} — Response: {detail} | Retry failed: {http_err2} — Response: {second_detail}"
                )
            else:
                text2 = r2.json()["choices"][0]["message"]["content"]
                return _clean_thinking(text2) if hide_thinking else text2

        raise requests.HTTPError(f"{http_err} — Response: {detail}")

    response_json = r.json()
    text = response_json["choices"][0]["message"]["content"]
    
    # Debug: check if response was truncated
    if os.getenv("XAI_DEBUG"):
        finish_reason = response_json["choices"][0].get("finish_reason", "unknown")
        usage = response_json.get("usage", {})
        print(f"[xai-debug] finish_reason={finish_reason}")
        print(f"[xai-debug] usage={usage}")
        if finish_reason == "length":
            print(f"[xai-debug] Response was truncated due to max_tokens limit")
    
    return _clean_thinking(text) if hide_thinking else text
