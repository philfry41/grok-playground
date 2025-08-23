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
        session['scene_state'] = {
            'characters': {},  # Dynamic character tracking
            'location': 'classroom',
            'positions': 'standing',
            'physical_contact': 'none'
        }
    
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
        # Handle /loadopener command - SIMPLIFIED VERSION
        filename = data.get('filename', 'opener.txt')
        print(f"🔍 Debug: filename='{filename}'")
        
        try:
            abs_path = os.path.abspath(filename)
            print(f"🔍 Debug: abs_path='{abs_path}'")
            
            # Check if file exists
            if not os.path.exists(filename):
                print(f"🔍 Debug: File {filename} does not exist, creating default opener")
                # Create a simple default opener
                opener = """A woman sat at her desk after hours, hearing footsteps in the hall. Her lips curled into a small smile as she'd been flirting with her colleague all day and knew he was taking her bait. The building's hum filled the quiet. She quickly removed her panties and shoved them in the drawer just before he entered her office. The smell of his cologne signaled his impending entrance..."""
                print(f"🔍 Debug: Using default opener, length={len(opener)}")
            else:
                # Try to read file with better error handling
                try:
                    with open(filename, "r", encoding="utf-8") as f:
                        opener = f.read()
                    print(f"🔍 Debug: opener length={len(opener)}")
                except UnicodeDecodeError as e:
                    print(f"🔍 Debug: Unicode decode error: {e}")
                    return jsonify({'error': f'File encoding error: {str(e)}'})
                except PermissionError as e:
                    print(f"🔍 Debug: Permission error: {e}")
                    return jsonify({'error': f'Permission denied reading {filename}: {str(e)}'})
                except Exception as e:
                    print(f"🔍 Debug: File read error: {e}")
                    return jsonify({'error': f'Error reading {filename}: {str(e)}'})
            
            byte_len = len(opener.encode("utf-8"))
            if byte_len == 0 or not any(ch.strip() for ch in opener):
                return jsonify({'error': f'{filename} looks empty. Path: {abs_path} (bytes={byte_len})'})
            
            # Add the opener content as a user message
            session['history'].append({"role": "user", "content": opener})
            
            print(f"🔍 Debug: About to return simple response")
            return jsonify({
                'message': f'📄 Loaded opener from {abs_path} (bytes={byte_len})',
                'type': 'system',
                'opener_content': opener,
                'ai_response': 'Click "Send" to continue the story...',
                'response_type': 'system'
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
        
        # Create a proper continuation prompt
        user_input = f"Continue the story naturally from where it left off. Write approximately {target} words with detailed, explicit content. Keep the scene flowing without natural stopping points."
        session['max_tokens'] = max_tokens
    
    # Add user message to history
    session['history'].append({"role": "user", "content": user_input})
    
    # Add scene state reminder to help maintain continuity
    characters_state = session.get('scene_state', {}).get('characters', {})
    character_list = []
    for char_name, char_state in characters_state.items():
        character_list.append(f"- {char_name}: {char_state.get('clothing', 'unknown')}")
    
    if not character_list:
        character_list = ["- No characters tracked yet"]
    
    scene_state_reminder = f"""
CURRENT SCENE STATE (maintain this continuity):
{chr(10).join(character_list)}
- Location: {session.get('scene_state', {}).get('location', 'unknown')}
- Positions: {session.get('scene_state', {}).get('positions', 'unknown')}
- Physical contact: {session.get('scene_state', {}).get('physical_contact', 'unknown')}

Continue the story while maintaining this physical state. Do not have clothes magically reappear or positions change without explicit action.
"""
    
    # Add scene state reminder as a system message
    session['history'].append({"role": "system", "content": scene_state_reminder})
    
    try:
        # Get model from environment
        model_env = os.getenv("XAI_MODEL", "grok-3")
        api_key = os.getenv("XAI_API_KEY")
        
        print(f"🔍 Debug: Model: {model_env}")
        print(f"🔍 Debug: API Key set: {'Yes' if api_key else 'No'}")
        print(f"🔍 Debug: Starting AI call with {len(session['history'])} messages")
        
                    # Limit history length for Render free tier
            if len(session['history']) > 8:  # Reduced from 10 to 8
                print(f"🔍 Debug: Truncating history from {len(session['history'])} to 8 messages")
                # Keep system messages and last 6 messages (reduced from 8)
                system_messages = [m for m in session['history'] if m['role'] == 'system']
                recent_messages = session['history'][-6:]
                session['history'] = system_messages + recent_messages
                print(f"🔍 Debug: History truncated to {len(session['history'])} messages")
        
        # Try with reduced complexity first
        try:
            print(f"🔍 Debug: Attempting first AI call...")
            reply = chat_with_grok(
                session['history'],
                model=model_env,
                temperature=0.9,  # Slightly lower for stability
                max_tokens=min(500, session['max_tokens']),  # Even more reduced tokens
                top_p=0.8,
                hide_thinking=True,
            )
            print(f"🔍 Debug: AI call successful, reply length={len(reply)}")
        except Exception as timeout_error:
            print(f"🔍 Debug: First attempt failed: {timeout_error}")
            print(f"🔍 Debug: Error type: {type(timeout_error)}")
            # Try with even simpler request
            simple_history = [
                {"role": "system", "content": "You are an explicit storyteller. Use detailed, sensual language."},
                {"role": "user", "content": f"Continue this story: {user_input}"}
            ]
            print(f"🔍 Debug: Attempting fallback AI call...")
            reply = chat_with_grok(
                simple_history,
                model=model_env,
                temperature=0.7,  # Lower for stability
                max_tokens=300,  # Very short for Render
                top_p=0.7,
                hide_thinking=True,
            )
            print(f"🔍 Debug: Fallback AI call successful, reply length={len(reply)}")
        
        # Add response to history
        session['history'].append({"role": "assistant", "content": reply})
        
        # Update scene state based on the response (simple keyword detection)
        scene_state = session.get('scene_state', {})
        reply_lower = reply.lower()
        
        # Update clothing states (dynamic character tracking)
        if 'removed' in reply_lower or 'took off' in reply_lower or 'stripped' in reply_lower:
            print(f"🔍 Debug: Clothing removal detected in response")
            # Extract character names from the response
            import re
            
            # Look for character names (capitalized words that could be names)
            potential_names = re.findall(r'\b[A-Z][a-z]+\b', reply)
            print(f"🔍 Debug: Potential names found: {potential_names}")
            
            # Common clothing items and their associations
            clothing_items = {
                'panties': 'underwear removed',
                'underwear': 'underwear removed', 
                'bra': 'bra removed',
                'shirt': 'shirt removed',
                'blouse': 'blouse removed',
                'top': 'top removed',
                'pants': 'pants removed',
                'slacks': 'pants removed',
                'trousers': 'pants removed',
                'dress': 'dress removed',
                'skirt': 'skirt removed'
            }
            
            # Track characters mentioned in the response
            for name in potential_names:
                if name not in ['The', 'She', 'He', 'Her', 'His', 'They', 'Their']:
                    print(f"🔍 Debug: Processing character: {name}")
                    if name not in scene_state['characters']:
                        scene_state['characters'][name] = {'clothing': 'fully dressed'}
                        print(f"🔍 Debug: Created new character entry for {name}")
                    
                    # Check what clothing was removed for this character
                    for item, state in clothing_items.items():
                        if item in reply_lower:
                            print(f"🔍 Debug: Clothing item '{item}' found in response")
                            # Look for patterns like "Sarah removed her panties" or "Mike took off his shirt"
                            # Also check for simpler patterns like "Sarah's panties" or "Mike's shirt"
                            pattern1 = rf'\b{name}\b.*\b{item}\b'
                            pattern2 = rf'\b{name}\'s\s+{item}\b'
                            if re.search(pattern1, reply_lower) or re.search(pattern2, reply_lower):
                                scene_state['characters'][name]['clothing'] = state
                                print(f"🔍 Debug: Updated {name}'s clothing to {state}")
                                break
        
        # Update positions
        if 'sitting' in reply_lower or 'sat' in reply_lower:
            scene_state['positions'] = 'sitting'
        elif 'lying' in reply_lower or 'laid' in reply_lower or 'on her back' in reply_lower:
            scene_state['positions'] = 'lying down'
        elif 'kneeling' in reply_lower or 'on her knees' in reply_lower:
            scene_state['positions'] = 'kneeling'
        
        # Update physical contact
        if 'kiss' in reply_lower or 'kissing' in reply_lower:
            scene_state['physical_contact'] = 'kissing'
        elif 'touch' in reply_lower or 'touching' in reply_lower:
            scene_state['physical_contact'] = 'touching'
        elif 'penetration' in reply_lower or 'inside' in reply_lower:
            scene_state['physical_contact'] = 'penetration'
        
        session['scene_state'] = scene_state
        print(f"🔍 Debug: Updated scene state: {scene_state}")
        
                    # Clean up session if it gets too large (Render memory management)
            if len(session['history']) > 8:  # Reduced from 12 to 8
                print(f"🔍 Debug: Session cleanup - history has {len(session['history'])} messages")
                # Keep system messages and last 4 messages (reduced from 6)
                system_messages = [m for m in session['history'] if m['role'] == 'system']
                recent_messages = session['history'][-4:]
                session['history'] = system_messages + recent_messages
                print(f"🔍 Debug: Session cleaned up to {len(session['history'])} messages")
        
        # Handle TTS if enabled (simplified)
        audio_file = None
        if tts.enabled and reply.strip():
            try:
                # Only generate TTS for shorter responses on Render
                if len(reply) < 800:  # Even shorter for Render
                    audio_file = tts.speak(reply, save_audio=True)
                    print(f"🔍 Debug: TTS generated: {audio_file}")
            except Exception as e:
                print(f"🔍 Debug: TTS error: {e}")
        
        return jsonify({
            'message': reply,
            'type': 'assistant',
            'edge_triggered': False,  # Simplified for Render
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
        model_env = os.getenv('XAI_MODEL', 'grok-3')
        
        print(f"🔍 Debug: Test API - Model: {model_env}")
        print(f"🔍 Debug: Test API - API Key set: {'Yes' if api_key else 'No'}")
        
        if not api_key:
            return jsonify({'error': 'XAI_API_KEY not set'})
        
        # Test with a simple message
        test_messages = [
            {"role": "user", "content": "Say 'Hello, API test successful!'"}
        ]
        
        try:
            print(f"🔍 Debug: Test API - Attempting simple call...")
            response = chat_with_grok(test_messages, max_tokens=50)
            print(f"🔍 Debug: Test API - Success: {response}")
            return jsonify({
                'success': True,
                'response': response,
                'api_key_set': True,
                'model': model_env
            })
        except Exception as api_error:
            print(f"🔍 Debug: Test API - Error: {api_error}")
            print(f"🔍 Debug: Test API - Error type: {type(api_error)}")
            return jsonify({
                'success': False,
                'error': str(api_error),
                'api_key_set': True,
                'model': model_env
            })
            
    except Exception as e:
        print(f"🔍 Debug: Test API - Outer error: {e}")
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
