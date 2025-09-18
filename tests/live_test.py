#!/usr/bin/env python3
import os
import sys
import time
import json
import textwrap
import argparse
import csv
import math
from xml.sax.saxutils import escape as xml_escape
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


def run_suite(prompts: List[str], beats_values: List[int], max_tokens: int, 
              min_sentences_factor: float = 1.2,
              min_chars_per_beat: int = 60) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for p in prompts:
        # start fresh
        clear_active_scene()
        # seed with opener if your service expects it; otherwise continue directly
        for b in beats_values:
            data = send_chat(p, beats=b, max_tokens=max_tokens)
            ai_text = data.get("ai_response") or data.get("message") or ""
            summary = summarize_reply(ai_text)
            # Assertions
            expected_min_sentences = max(1, math.ceil(b * min_sentences_factor))
            expected_min_chars = b * min_chars_per_beat
            passed = True
            fail_reasons = []
            if summary["sentences"] < expected_min_sentences:
                passed = False
                fail_reasons.append(f"sentences {summary['sentences']} < min {expected_min_sentences}")
            if summary["chars"] < expected_min_chars:
                passed = False
                fail_reasons.append(f"chars {summary['chars']} < min {expected_min_chars}")

            case = {
                "prompt": p,
                "beats": b,
                "max_tokens": max_tokens,
                "ai_summary": summary,
                "raw_sample": ai_text[:400],
                "passed": passed,
                "fail_reasons": ", ".join(fail_reasons),
                "finish_reason": data.get("finish_reason"),
                "usage": data.get("usage"),
                "status_code": data.get("status_code"),
            }
            results.append(case)
            print(f"[beats={b}] sentences={summary['sentences']} chars={summary['chars']} :: {summary['preview']}")
            # small pause to avoid rate limits
            time.sleep(1.0)
    return results


def write_json(results: List[Dict[str, Any]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def write_csv(results: List[Dict[str, Any]], path: str) -> None:
    fieldnames = [
        "prompt", "beats", "max_tokens", "sentences", "chars", "passed", "fail_reasons",
        "finish_reason", "status_code"
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            row = {
                "prompt": r["prompt"],
                "beats": r["beats"],
                "max_tokens": r.get("max_tokens"),
                "sentences": r["ai_summary"]["sentences"],
                "chars": r["ai_summary"]["chars"],
                "passed": r["passed"],
                "fail_reasons": r.get("fail_reasons", ""),
                "finish_reason": r.get("finish_reason"),
                "status_code": r.get("status_code"),
            }
            w.writerow(row)


def write_junit(results: List[Dict[str, Any]], path: str) -> None:
    # Minimal JUnit XML
    total = len(results)
    failures = sum(1 for r in results if not r.get("passed", True))
    testsuite_attrs = f'tests="{total}" failures="{failures}" name="live_beats_tests"'
    parts = [f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<testsuite {testsuite_attrs}>"]
    for i, r in enumerate(results, 1):
        name = f"beats={r['beats']} tokens={r.get('max_tokens')}"
        prompt_excerpt = (r["prompt"][:60] + "…") if len(r["prompt"]) > 60 else r["prompt"]
        tc_open = f"  <testcase classname=\"live\" name=\"{xml_escape(name)}\" time=\"0\">"
        parts.append(tc_open)
        if not r.get("passed", True):
            msg = r.get("fail_reasons", "failed")
            cdata = xml_escape(msg)
            parts.append(f"    <failure message=\"{cdata}\"><![CDATA[{prompt_excerpt}]]></failure>")
        parts.append("  </testcase>")
    parts.append("</testsuite>\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def main():
    parser = argparse.ArgumentParser(description="Run live beats tests against Render deployment")
    parser.add_argument("--prompts-file", help="Path to a text file with one prompt per line")
    parser.add_argument("--prompt", action="append", help="Add a prompt directly (may be repeated)")
    parser.add_argument("--beats", type=int, nargs="+", default=[1, 2, 4], help="Beats to test, e.g. --beats 1 2 4")
    parser.add_argument("--max-tokens", type=int, default=int(os.getenv("MAX_TOKENS", "1200")), help="Token budget per turn")
    parser.add_argument("--min-sentences-factor", type=float, default=float(os.getenv("MIN_SENTENCES_FACTOR", "1.2")),
                        help="Minimum sentences = ceil(beats * factor)")
    parser.add_argument("--min-chars-per-beat", type=int, default=int(os.getenv("MIN_CHARS_PER_BEAT", "60")),
                        help="Minimum characters required = beats * this value")
    parser.add_argument("--out-json", help="Write full JSON results to path")
    parser.add_argument("--out-csv", help="Write CSV summary to path")
    parser.add_argument("--out-junit", help="Write JUnit XML report to path")
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

    results = run_suite(
        prompts, args.beats, args.max_tokens,
        min_sentences_factor=args.min_sentences_factor,
        min_chars_per_beat=args.min_chars_per_beat,
    )

    # Print JSON to stdout for quick inspection
    print("\n=== JSON RESULTS ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))

    # Exports
    if args.out_json:
        write_json(results, args.out_json)
    if args.out_csv:
        write_csv(results, args.out_csv)
    if args.out_junit:
        write_junit(results, args.out_junit)

    # Exit non-zero if any failures
    if any(not r.get("passed", True) for r in results):
        sys.exit(2)


if __name__ == "__main__":
    main()
