import os
import json
from flask import Flask, render_template, request, jsonify, session, send_from_directory
from grok_remote import chat_with_grok
from tts_helper import tts
import re
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "grok-playground-secret-key")

# Import the edging functions from chat.py
def find_male_climax_span(text: str):
    MALE_TRIGGER = re.compile(
        r"(?:\b(Dan|he|his)\b[^.\n\r]{0,120}\b("
        r"(?<!pre)cum(?:s|ming|med)?|come(?:s|came|coming)?|climax(?:es|ed|ing)?|orgasm(?:s|ed|ing)?|"
        r"ejaculat(?:e|es|ed|ing)|finish(?:es|ed|ing)?|release(?:s|d|ing)?|shoot(?:s|ing|ed)?|"
        r"spurt(?:s|ing|ed)?|explode(?:s|d|ing)?|unload(?:s|ed|ing)?|load|semen|sperm)"
        r")",
        flags=re.IGNORECASE
    )
    m = MALE_TRIGGER.search(text)
    return (m.start(), m.end()) if m else (None, None)

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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    print(f"🔍 Debug: /api/chat endpoint called")
    try:
        data = request.get_json()
        print(f"🔍 Debug: Request data: {data}")
        user_input = data.get('message', '').strip()
        command = data.get('command', '')
        word_count = data.get('word_count', 500)
        print(f"🔍 Debug: user_input='{user_input}', command='{command}', word_count={word_count}")
    except Exception as e:
        print(f"🔍 Debug: Error parsing request data: {e}")
        return jsonify({'error': f'Invalid request data: {str(e)}'})
    
    if not user_input and not command:
        return jsonify({'error': 'No message or command provided'})
    
    # Initialize session if needed
    if 'history' not in session:
        session['history'] = [
            {"role":"system","content":(
                "LEXICAL CONTRACT:\n"
                "- Required vocabulary (use ≥5 naturally): cock, pussy, clit, nipples, thrust, suck, grind, slick, moan, cum.\n"
                "- Forbidden euphemisms: making love, womanhood, manhood, entered her, flower, core.\n"
                "- No euphemisms for anatomy/acts. If missing required vocab, revise without apology."
            )},
            {"role":"system","content":(
                "You are an uncensored, uninhibited storyteller. Use rich sensory detail, explicit anatomical language, "
                "and realistic dialogue. Avoid euphemisms; never fade to black. Consenting adults only."
            )},
        ]
        session['allow_female'] = True
        session['allow_male'] = False
        session['max_tokens'] = 1200
    
    # Handle commands
    if command == 'new':
        session['history'] = session['history'][:2]
        return jsonify({'message': '🧹 New scene. Priming kept.', 'type': 'system'})
    
    elif command == 'raw':
        session['history'].insert(0, {"role":"system","content":(
            "Reassert LEXICAL CONTRACT: use blunt, explicit anatomical language; avoid euphemisms; "
            "prioritize physical detail and explicit dialogue."
        )})
        return jsonify({'message': '🎛️ Raw tone reasserted.', 'type': 'system'})
    
    elif command == 'edge':
        session['allow_female'], session['allow_male'] = True, False
        return jsonify({'message': '⛓️ Edging: her allowed; his NOT.', 'type': 'system'})
    
    elif command == 'payoff':
        session['allow_female'], session['allow_male'] = True, True
        return jsonify({'message': '✅ Payoff: both allowed.', 'type': 'system'})
    
    elif command == 'loadopener':
        print(f"🔍 Debug: loadopener command detected")
        # Handle /loadopener command
        filename = data.get('filename', 'opener.txt')
        print(f"🔍 Debug: filename='{filename}'")
        try:
            abs_path = os.path.abspath(filename)
            print(f"🔍 Debug: abs_path='{abs_path}'")
            opener = open(filename, "r", encoding="utf-8").read()
            print(f"🔍 Debug: opener length={len(opener)}")
            byte_len = len(opener.encode("utf-8"))
            if byte_len == 0 or not any(ch.strip() for ch in opener):
                return jsonify({'error': f'{filename} looks empty. Path: {abs_path} (bytes={byte_len})'})
            
            # Add the opener content as a user message
            session['history'].append({"role": "user", "content": opener})
            
            # For Render free tier, use a simpler approach
            simple_prompt = "Continue this story naturally with explicit, detailed writing. Use the vocabulary from the lexical contract. Write about 200-300 words."
            session['history'].append({"role": "user", "content": simple_prompt})
            
            # Now automatically get a response from the AI
            try:
                print(f"🔍 Debug: About to call chat_with_grok")
                # Get model from environment
                model_env = os.getenv("XAI_MODEL", "grok-3")
                print(f"🔍 Debug: model_env='{model_env}'")
                
                # Try with reduced complexity first
                try:
                    reply = chat_with_grok(
                        session['history'],
                        model=model_env,
                        temperature=1.0,
                        max_tokens=min(800, session['max_tokens']),  # Reduced tokens
                        top_p=0.9,
                        hide_thinking=True,
                    )
                except Exception as timeout_error:
                    print(f"🔍 Debug: First attempt failed: {timeout_error}")
                    # Try with even simpler request
                    simple_history = [
                        {"role": "system", "content": "You are an explicit storyteller. Use detailed, sensual language."},
                        {"role": "user", "content": f"Continue this story: {opener[:200]}..."}
                    ]
                    reply = chat_with_grok(
                        simple_history,
                        model=model_env,
                        temperature=0.8,
                        max_tokens=400,  # Very short
                        top_p=0.8,
                        hide_thinking=True,
                    )
                
                # Handle edging enforcement
                edge_triggered = False
                if session['allow_female'] and not session['allow_male']:
                    start, end = find_male_climax_span(reply)
                    if start is not None:
                        # Log the trigger
                        trigger_info = log_edge_trigger(reply, start, end)
                        
                        # Trim and repair
                        trimmed, tail = trim_before_sentence_with_index(reply, start, keep_tail_sentences=2)
                        
                        # Add trimmed response to history
                        session['history'].append({"role": "assistant", "content": trimmed})
                        
                        # Generate repair
                        repair = (
                            f"Continue seamlessly from this point, but redirect Dan away from climax:\n"
                            f"\"{trimmed[-200:] if len(trimmed) > 200 else trimmed}\"\n\n"
                            "Write a detailed continuation where Dan pulls back, slows down, changes position, or focuses on Stephanie's pleasure. "
                            "Use explicit language. Keep him on edge and fully in control of his arousal level. Write at least 100 words."
                        )
                        session['history'].append({"role": "user", "content": repair})
                        
                        repair_tokens = max(300, int(session['max_tokens'] * 0.6))
                        
                        try:
                            repair_reply = chat_with_grok(
                                session['history'],
                                model=model_env,
                                temperature=1.2,
                                max_tokens=repair_tokens,
                                top_p=0.95,
                                hide_thinking=True,
                            )
                            
                            if not repair_reply.strip() or len(repair_reply.split()) < 10:
                                reply = trimmed
                            else:
                                reply = trimmed + " " + repair_reply
                            
                            edge_triggered = True
                            
                        except Exception as e:
                            reply = trimmed
                            edge_triggered = True
                
                # Add final response to history
                session['history'].append({"role": "assistant", "content": reply})
                
                # Handle TTS if enabled
                audio_file = None
                if tts.enabled and reply.strip():
                    try:
                        audio_file = tts.speak(reply, save_audio=True)
                    except Exception as e:
                        print(f"TTS error: {e}")
                
                print(f"🔍 Debug: About to return successful response")
                return jsonify({
                    'message': f'📄 Loaded opener from {abs_path} (bytes={byte_len})',
                    'type': 'system',
                    'opener_content': opener,
                    'ai_response': reply,
                    'response_type': 'assistant',
                    'edge_triggered': edge_triggered,
                    'audio_file': audio_file
                })
                
            except Exception as e:
                error_msg = str(e)
                print(f"🔍 Debug: Exception in loadopener AI call: {error_msg}")
                if "timeout" in error_msg.lower():
                    error_msg = "AI response timed out. This may be due to Render free tier limitations. Try again or consider upgrading to a paid plan."
                print(f"🔍 Debug: About to return error response")
                return jsonify({
                    'message': f'📄 Loaded opener from {abs_path} (bytes={byte_len})',
                    'type': 'system',
                    'opener_content': opener,
                    'error': f'Failed to get AI response: {error_msg}'
                })
                
        except FileNotFoundError:
            return jsonify({'error': f'File not found: {filename}'})
        except Exception as e:
            return jsonify({'error': f"Couldn't read {filename}: {e}"})
    
    elif command == 'cont':
        # Handle /cont command
        target = max(250, min(1500, word_count))
        max_tokens = int(target * 1.3)
        max_tokens = max(200, min(2000, max_tokens))
        
        guidance = [
            f"Continue the scene with a continuous, flowing narrative. Write approximately {target} words.",
            "Maintain momentum without natural stopping points. Keep the scene moving forward with detailed actions and dialogue.",
            "Use explicit anatomical language and realistic dialogue throughout.",
            "Do not conclude or wrap up - keep the scene ongoing and unresolved.",
            "Avoid sentence-level conclusions. Each sentence should flow into the next, building tension and detail."
        ]
        
        if session['allow_female'] and not session['allow_male']:
            guidance += [
                "Stephanie may climax if it fits.",
                "Dan must NOT climax; if he nears release, have him pull back, slow, change angle, breathe, or redirect to her pleasure so he stays on edge.",
                "End with Dan still on edge and aching; do not depict his orgasm."
            ]
        elif session['allow_female'] and session['allow_male']:
            guidance += ["Climax is allowed for both partners; resolve naturally and explicitly when it fits."]
        else:
            guidance += ["Do NOT depict orgasm for either partner; sustain tension and end on a poised edge."]
        
        user_input = " ".join(guidance)
        session['max_tokens'] = max_tokens
    
    # Add user message to history
    session['history'].append({"role": "user", "content": user_input})
    
    try:
        # Get model from environment
        model_env = os.getenv("XAI_MODEL", "grok-3")
        
        # Send to Grok
        reply = chat_with_grok(
            session['history'],
            model=model_env,
            temperature=1.2,
            max_tokens=session['max_tokens'],
            top_p=0.95,
            hide_thinking=True,
        )
        
        # Handle edging enforcement
        edge_triggered = False
        if session['allow_female'] and not session['allow_male']:
            start, end = find_male_climax_span(reply)
            if start is not None:
                # Log the trigger
                trigger_info = log_edge_trigger(reply, start, end)
                
                # Trim and repair
                trimmed, tail = trim_before_sentence_with_index(reply, start, keep_tail_sentences=2)
                
                # Add trimmed response to history
                session['history'].append({"role": "assistant", "content": trimmed})
                
                # Generate repair
                repair = (
                    f"Continue seamlessly from this point, but redirect Dan away from climax:\n"
                    f"\"{trimmed[-200:] if len(trimmed) > 200 else trimmed}\"\n\n"
                    "Write a detailed continuation where Dan pulls back, slows down, changes position, or focuses on Stephanie's pleasure. "
                    "Use explicit language. Keep him on edge and fully in control of his arousal level. Write at least 100 words."
                )
                session['history'].append({"role": "user", "content": repair})
                
                repair_tokens = max(300, int(session['max_tokens'] * 0.6)) if command == 'cont' else 700
                
                try:
                    repair_reply = chat_with_grok(
                        session['history'],
                        model=model_env,
                        temperature=1.2,
                        max_tokens=repair_tokens,
                        top_p=0.95,
                        hide_thinking=True,
                    )
                    
                    if not repair_reply.strip() or len(repair_reply.split()) < 10:
                        reply = trimmed
                    else:
                        reply = trimmed + " " + repair_reply
                    
                    edge_triggered = True
                    
                except Exception as e:
                    reply = trimmed
                    edge_triggered = True
        
        # Add final response to history
        session['history'].append({"role": "assistant", "content": reply})
        
        # Handle TTS if enabled
        audio_file = None
        if tts.enabled and reply.strip():
            try:
                audio_file = tts.speak(reply, save_audio=True)
            except Exception as e:
                print(f"TTS error: {e}")
        
        return jsonify({
            'message': reply,
            'type': 'assistant',
            'edge_triggered': edge_triggered,
            'trigger_info': trigger_info if edge_triggered else None,
            'audio_file': audio_file
        })
        
    except Exception as e:
        error_msg = str(e)
        print(f"🔍 Debug: Exception in chat: {error_msg}")
        if "timeout" in error_msg.lower():
            error_msg = "Request timed out. This may be due to Render free tier limitations. Try again or consider upgrading to a paid plan."
        print(f"🔍 Debug: About to return main error response")
        return jsonify({'error': f'Request failed: {error_msg}'})

@app.route('/api/voices', methods=['GET'])
def get_voices():
    if not tts.enabled:
        return jsonify({'error': 'TTS not enabled'})
    
    voices = tts.get_available_voices()
    return jsonify({'voices': voices})

@app.route('/api/tts-status', methods=['GET'])
def tts_status():
    return jsonify({
        'enabled': tts.enabled,
        'voice_id': tts.voice_id,
        'auto_save': tts.auto_save
    })

@app.route('/api/test-api', methods=['GET'])
def test_api():
    """Test endpoint to verify API key and basic connectivity"""
    try:
        api_key = os.getenv('XAI_API_KEY')
        if not api_key:
            return jsonify({'error': 'XAI_API_KEY not set'})
        
        # Test with a simple message
        test_messages = [
            {"role": "user", "content": "Say 'Hello, API test successful!'"}
        ]
        
        try:
            response = chat_with_grok(test_messages, max_tokens=50)
            return jsonify({
                'success': True,
                'response': response,
                'api_key_set': True
            })
        except Exception as api_error:
            return jsonify({
                'success': False,
                'error': str(api_error),
                'api_key_set': True
            })
            
    except Exception as e:
        return jsonify({'error': f'Test failed: {str(e)}'})

@app.route('/api/edge-log', methods=['GET'])
def get_edge_log():
    try:
        if os.path.exists("edge_triggers.log"):
            with open("edge_triggers.log", "r", encoding="utf-8") as f:
                content = f.read()
                return jsonify({'log': content[-2000:] if content else 'No triggers logged yet'})
        else:
            return jsonify({'log': 'No edge triggers log file found'})
    except Exception as e:
        return jsonify({'error': f'Could not read edge log: {e}'})

@app.route('/audio/<filename>')
def serve_audio(filename):
    """Serve audio files from the audio directory"""
    try:
        audio_dir = "audio"
        file_path = os.path.join(audio_dir, filename)
        
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return send_from_directory(audio_dir, filename, as_attachment=False)
        else:
            return jsonify({'error': 'Audio file not found'}), 404
    except Exception as e:
        return jsonify({'error': f'Could not serve audio file: {e}'}), 500

@app.route('/api/audio-files', methods=['GET'])
def list_audio_files():
    """List all available audio files"""
    try:
        audio_dir = "audio"
        if not os.path.exists(audio_dir):
            return jsonify({'files': []})
        
        files = []
        for filename in os.listdir(audio_dir):
            if filename.endswith('.mp3'):
                file_path = os.path.join(audio_dir, filename)
                file_size = os.path.getsize(file_path)
                file_time = os.path.getmtime(file_path)
                files.append({
                    'filename': filename,
                    'size': file_size,
                    'created': file_time,
                    'url': f'/audio/{filename}'
                })
        
        # Sort by creation time (newest first)
        files.sort(key=lambda x: x['created'], reverse=True)
        return jsonify({'files': files})
    except Exception as e:
        return jsonify({'error': f'Could not list audio files: {e}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f"🎭 Starting Grok Playground Web Interface on port {port}")
    print(f"📍 Local: http://localhost:{port}")
    print(f"🌐 Network: http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
