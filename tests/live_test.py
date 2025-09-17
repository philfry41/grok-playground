#!/usr/bin/env python3
import os
import sys
import time
import json
import textwrap
import argparse
from typing import List, Dict, Any

import requests

RENDER_URL = os.getenv("RENDER_URL", "https://grok-playground.onrender.com")
TEST_API_KEY = os.getenv("TEST_API_KEY", "")

HEADERS = {
    "Content-Type": "application/json",
}
if TEST_API_KEY:
    HEADERS["Authorization"] = f"Bearer {TEST_API_KEY}"


def post(path: str, payload: Dict[str, Any]) -> requests.Response:
    url = RENDER_URL.rstrip("/") + path
    return requests.post(url, headers=HEADERS, data=json.dumps(payload), timeout=60)


def get(path: str) -> requests.Response:
    url = RENDER_URL.rstrip("/") + path
    return requests.get(url, headers=HEADERS, timeout=30)


def clear_active_scene() -> None:
    r = post("/api/clear-active-scene", {})
    try:
        data = r.json()
    except Exception:
        data = {"status_code": r.status_code, "text": r.text[:2000]}
    print("[clear-active-scene]", json.dumps(data, ensure_ascii=False)[:300])


def send_chat(message: str, beats: int, max_tokens: int = 1200) -> Dict[str, Any]:
    payload = {
        "message": message,
        "beats": beats,
        "word_count": max_tokens,  # server interprets as token budget
    }
    r = post("/api/chat", payload)
    try:
        data = r.json()
    except Exception:
        data = {"status_code": r.status_code, "text": r.text[:2000]}
    return data


def summarize_reply(text: str) -> Dict[str, Any]:
    if not isinstance(text, str):
        return {"chars": 0, "sentences": 0, "preview": ""}
    sentences = [s for s in text.replace("\n", " ").split(".") if s.strip()]
    return {
        "chars": len(text),
        "sentences": len(sentences),
        "preview": textwrap.shorten(text, width=160, placeholder="…"),
    }


def run_suite(prompts: List[str], beats_values: List[int], max_tokens: int) -> None:
    results: List[Dict[str, Any]] = []
    for p in prompts:
        # start fresh
        clear_active_scene()
        # seed with opener if your service expects it; otherwise continue directly
        for b in beats_values:
            data = send_chat(p, beats=b, max_tokens=max_tokens)
            ai_text = data.get("ai_response") or data.get("message") or ""
            summary = summarize_reply(ai_text)
            results.append({
                "prompt": p,
                "beats": b,
                "ai_summary": summary,
                "raw_sample": ai_text[:400],
            })
            print(f"[beats={b}] sentences={summary['sentences']} chars={summary['chars']} :: {summary['preview']}")
            # small pause to avoid rate limits
            time.sleep(1.0)
    print("\n=== JSON RESULTS ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Run live beats tests against Render deployment")
    parser.add_argument("--prompts-file", help="Path to a text file with one prompt per line")
    parser.add_argument("--prompt", action="append", help="Add a prompt directly (may be repeated)")
    parser.add_argument("--beats", type=int, nargs="+", default=[1, 2, 4], help="Beats to test, e.g. --beats 1 2 4")
    parser.add_argument("--max-tokens", type=int, default=int(os.getenv("MAX_TOKENS", "1200")), help="Token budget per turn")
    args = parser.parse_args()

    if not RENDER_URL:
        print("RENDER_URL not set", file=sys.stderr)
        sys.exit(1)
    if not TEST_API_KEY:
        print("WARNING: TEST_API_KEY not set; protected routes may reject requests", file=sys.stderr)

    prompts: List[str] = []
    if args.prompts_file and os.path.exists(args.prompts_file):
        with open(args.prompts_file, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s:
                    prompts.append(s)
    if args.prompt:
        prompts.extend(args.prompt)
    if not prompts:
        prompts = ["Continue the story naturally."]

    run_suite(prompts, args.beats, args.max_tokens)


if __name__ == "__main__":
    main()
