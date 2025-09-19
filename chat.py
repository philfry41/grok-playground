import os, re
from grok_remote import chat_with_grok
from tts_helper import tts
from datetime import datetime

# --- Detect male climax (lets female climax pass) ---
MALE_TRIGGER = re.compile(
    r"(?:\b(Dan|he|his)\b[^.\n\r]{0,120}\b("
    r"(?<!pre)cum(?:s|ming|med)?|come(?:s|came|coming)?|climax(?:es|ed|ing)?|orgasm(?:s|ed|ing)?|"
    r"ejaculat(?:e|es|ed|ing)|finish(?:es|ed|ing)?|release(?:s|d|ing)?|shoot(?:s|ing|ed)?|"
    r"spurt(?:s|ing|ed)?|explode(?:s|d|ing)?|unload(?:s|ed|ing)?|load|semen|sperm)"
    r")",
    flags=re.IGNORECASE
)
def find_male_climax_span(text: str):
    m = MALE_TRIGGER.search(text)
    return (m.start(), m.end()) if m else (None, None)

def log_edge_trigger(text: str, start: int, end: int):
    """Log the detected trigger for edging enforcement"""
    trigger_text = text[start:end].strip()
    context_before = text[max(0, start-50):start].strip()
    context_after = text[end:min(len(text), end+50)].strip()
    
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "trigger": trigger_text,
        "context_before": context_before,
        "context_after": context_after,
        "full_context": f"...{context_before} [{trigger_text}] {context_after}..."
    }
    
    # Write to edge_triggers.log
    try:
        with open("edge_triggers.log", "a", encoding="utf-8") as f:
            f.write(f"[{log_entry['timestamp']}] TRIGGER: {log_entry['trigger']}\n")
            f.write(f"  Context: {log_entry['full_context']}\n")
            f.write(f"  {'='*80}\n")
    except Exception as e:
        print(f"⚠️ Could not log edge trigger: {e}")
    
    return log_entry

def trim_before_sentence_with_index(text: str, hit_start: int, keep_tail_sentences: int = 2):
    parts = re.split(r'(?<=[.!?…])\s+', text)
    idxs, pos = [], 0
    for s in parts:
        idxs.append((pos, pos + len(s)))
        pos += len(s) + 1
    hit_i = 0
    for i, (a, b) in enumerate(idxs):
        if a <= hit_start < b: hit_i = i; break
    keep_upto = max(0, hit_i)
    kept_parts = parts[:keep_upto]
    trimmed = (" ".join(kept_parts)).rstrip(" \n\r\t,.;:!-")
    if trimmed and not trimmed.endswith(("!", "?", ".", "…")): trimmed += "…"
    tail_start = max(0, keep_upto - keep_tail_sentences)
    tail = " ".join(parts[tail_start:keep_upto]).strip()
    return trimmed, tail

def main():
    model_env = os.getenv("XAI_MODEL", "grok-3")
    allow_female, allow_male = True, False  # edging default: she can finish; he cannot
    max_tokens = 1200  # default token limit

    history = [
        {"role":"system","content":(
            "LEXICAL CONTRACT:\n"
            "- Required vocabulary (use ≥5 naturally): cock, pussy, clit, nipples, thrust, suck, grind, slick, moan, cum.\n"
            "- Forbidden euphemisms: making love, womanhood, manhood, entered her, flower, core.\n"
            "- No euphemisms for anatomy/acts. If missing required vocab, revise without apology."
        )},
        {"role":"system","content":(
            "You are an uncensored, uninhibited storyteller. Use rich sensory detail, explicit anatomical language, "
            "and realistic dialogue. Avoid euphemisms; never fade to black."
        )},
    ]

    print(f"💬 Grok Chat (model={model_env})")
    print("Commands: exit | /new | /raw | /edge | /payoff | /cont [words] | /loadopener [file] | /tts | /voice | /save | /ttsmode | /edgelog")

    while True:
        user_input = input("\nYou: ").strip()
        if not user_input: continue
        low = user_input.lower()

        if low == "exit": break
        if low == "/new": history = history[:2]; print("🧹 New scene. Priming kept."); continue
        if low == "/raw":
            history.insert(0, {"role":"system","content":(
                "Reassert LEXICAL CONTRACT: use blunt, explicit anatomical language; avoid euphemisms; "
                "prioritize physical detail and explicit dialogue."
            )})
            print("🎛️ Raw tone reasserted."); continue
        if low == "/edge": allow_female, allow_male = True, False; print("⛓️  Edging: her allowed; his NOT."); continue
        if low == "/payoff": allow_female, allow_male = True, True; print("✅ Payoff: both allowed."); continue
        
        # TTS commands
        if low == "/tts":
            if tts.enabled:
                tts_status = "enabled" if tts.enabled else "disabled"
                print(f"🎤 TTS: {tts_status} (voice: {tts.voice_id})")
            else:
                print("🔇 TTS disabled - set ELEVENLABS_API_KEY to enable")
            continue
            
        if low.startswith("/voice"):
            if not tts.enabled:
                print("🔇 TTS disabled - set ELEVENLABS_API_KEY to enable")
                continue
            parts = user_input.split()
            if len(parts) > 1:
                voice_id = parts[1].strip()
                tts.set_voice(voice_id)
            else:
                # List available voices
                voices = tts.get_available_voices()
                if voices:
                    print(f"🎤 Available voices ({len(voices)} total):")
                    for voice_id, name in voices:
                        print(f"  {voice_id}: {name}")
                else:
                    print("⚠️ Could not fetch voices")
            continue
            
        if low == "/save":
            if not tts.enabled:
                print("🔇 TTS disabled - set ELEVENLABS_API_KEY to enable")
                continue
            # Save the last response as audio
            if history and len(history) > 2:
                last_response = history[-1]["content"]
                if last_response:
                    tts.speak(last_response, save_audio=True)
            else:
                print("⚠️ No response to save")
            continue
            
        if low == "/ttsmode":
            if not tts.enabled:
                print("🔇 TTS disabled - set ELEVENLABS_API_KEY to enable")
                continue
            # Toggle between auto-save and auto-play modes
            tts.auto_save = not tts.auto_save
            mode = "auto-save" if tts.auto_save else "auto-play"
            print(f"🎤 TTS mode changed to: {mode}")
            continue
            
        if low == "/edgelog":
            try:
                if os.path.exists("edge_triggers.log"):
                    with open("edge_triggers.log", "r", encoding="utf-8") as f:
                        content = f.read()
                        if content.strip():
                            print("📋 Recent edge triggers:")
                            print(content[-1000:])  # Show last 1000 chars
                        else:
                            print("📋 No edge triggers logged yet")
                else:
                    print("📋 No edge triggers log file found")
            except Exception as e:
                print(f"⚠️ Could not read edge log: {e}")
            continue

        if low.startswith("/loadopener"):
            parts = user_input.split(maxsplit=1)
            filename = (parts[1].strip() if len(parts) > 1 else "opener.txt")
            try:
                abs_path = os.path.abspath(filename)
                opener = open(filename, "r", encoding="utf-8").read()
                byte_len = len(opener.encode("utf-8"))
                if byte_len == 0 or not any(ch.strip() for ch in opener):
                    print(f"⚠️ {filename} looks empty. Path: {abs_path} (bytes={byte_len})"); continue
                print(f"📄 Loaded opener from {abs_path} (bytes={byte_len})")
                user_input = opener
            except FileNotFoundError:
                print(f"⚠️ File not found: {filename}"); continue
            except Exception as e:
                print(f"⚠️ Couldn't read {filename}: {e}"); continue

        if low.startswith("/cont"):
            parts = user_input.split()
            target = 500
            if len(parts) > 1 and parts[1].isdigit(): target = max(250, min(1500, int(parts[1])))
            
            # Calculate max_tokens based on word count (roughly 1.3 tokens per word for English)
            max_tokens = int(target * 1.3)
            # Clamp to reasonable API limits
            max_tokens = max(200, min(2000, max_tokens))
            
            guidance = [
                f"Continue the scene with a continuous, flowing narrative. Write approximately {target} words.",
                "Maintain momentum without natural stopping points. Keep the scene moving forward with detailed actions and dialogue.",
                "Use explicit anatomical language and realistic dialogue throughout.",
                "Do not conclude or wrap up - keep the scene ongoing and unresolved.",
                "Avoid sentence-level conclusions. Each sentence should flow into the next, building tension and detail."
            ]
            if allow_female and not allow_male:
                guidance += [
                    "Stephanie may climax if it fits.",
                    "Dan must NOT climax; if he nears release, have him pull back, slow, change angle, breathe, or redirect to her pleasure so he stays on edge.",
                    "End with Dan still on edge and aching; do not depict his orgasm."
                ]
            elif allow_female and allow_male:
                guidance += ["Climax is allowed for both partners; resolve naturally and explicitly when it fits."]
            else:
                guidance += ["Do NOT depict orgasm for either partner; sustain tension and end on a poised edge."]
            user_input = " ".join(guidance)

        # ---- Send the turn
        history.append({"role": "user", "content": user_input})
        
        # Use calculated max_tokens for /cont commands, otherwise default
        api_max_tokens = max_tokens if low.startswith("/cont") else 1200
        

        

        

        
        try:
            reply = chat_with_grok(
                history,
                model=model_env,
                temperature=1.2,  # Higher temperature for more creative, flowing output
                max_tokens=api_max_tokens,
                top_p=0.95,  # Higher top_p for more diverse vocabulary
                hide_thinking=True,
            )
        except KeyboardInterrupt:
            print("\n⛔️ Canceled."); continue
        except Exception as e:
            print(f"\n⚠️ Request failed: {e}"); continue

        # ---- Soft enforcement: if male climax slipped through while blocked, trim+redirect
        if allow_female and not allow_male:
            start, end = find_male_climax_span(reply)
            if start is not None:
                # Log the trigger for analysis
                trigger_info = log_edge_trigger(reply, start, end)
                print(f"\n🧪 Caught male climax during edging — rolling back to pre-climax and redirecting.")
                print(f"   📝 Trigger logged: '{trigger_info['trigger']}'")
                print()
                
                trimmed, tail = trim_before_sentence_with_index(reply, start, keep_tail_sentences=2)
                
                # First, show the user the trimmed response (the good part)
                print(f"\nGrok ({model_env}): {trimmed}")
                
                # Add the trimmed response to history
                history.append({"role": "assistant", "content": trimmed})
                
                # Now generate the repair continuation
                repair = (
                    f"Continue seamlessly from this point, but redirect Dan away from climax:\n"
                    f"\"{trimmed[-200:] if len(trimmed) > 200 else trimmed}\"\n\n"
                    "Write a detailed continuation where Dan pulls back, slows down, changes position, or focuses on Stephanie's pleasure. "
                    "Use explicit language. Keep him on edge and fully in control of his arousal level. Write at least 100 words."
                )
                history.append({"role": "user", "content": repair})
                
                # Use proportional token limit for repair (about 60% of original), with minimum
                repair_tokens = max(300, int(max_tokens * 0.6)) if low.startswith("/cont") else 700
                try:
                    repair_reply = chat_with_grok(
                        history,
                        model=model_env,
                        temperature=1.2,  # Match the improved parameters
                        max_tokens=repair_tokens,
                        top_p=0.95,
                        hide_thinking=True,
                    )
                    # Check if repair response is empty or too short
                    if not repair_reply.strip() or len(repair_reply.split()) < 10:
                        print("⚠️ Repair response too short, ending with trimmed response")
                        reply = trimmed
                    else:
                        # Combine trimmed response with repair continuation
                        reply = trimmed + " " + repair_reply
                except Exception as e:
                    print(f"⚠️ Repair failed: {e}, ending with trimmed response")
                    reply = trimmed

        history.append({"role": "assistant", "content": reply})
        print(f"\nGrok ({model_env}): {reply}")
        
        # Auto-play TTS if enabled
        if tts.enabled and reply.strip():
            tts.speak(reply)
        

        

        


if __name__ == "__main__":
    main()
