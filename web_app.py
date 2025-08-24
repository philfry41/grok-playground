import os
import json
from flask import Flask, render_template, request, jsonify, session, send_from_directory
from grok_remote import chat_with_grok
from story_state_manager import StoryStateManager
from tts_helper import tts
import re
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "grok-playground-secret-key")

# Import the edging functions from chat.py
def find_male_climax_span(text: str):
    MALE_TRIGGER = re.compile(
        r"(?:\b(Dan|he|his)\b[^.\n\r]{0,120}\b("
        r"(?:pre)?cum(?:s|ming|med)?|come(?:s|came|coming)?|climax(?:es|ed|ing)?|orgasm(?:s|ed|ing)?|"
        r"ejaculat(?:e|es|ed|ing)|finish(?:es|ed|ing)?|release(?:s|d|ing)?|shoot(?:s|ing|ed)?|"
        r"spurt(?:s|ing|ed)?|explode(?:s|d|ing)?|unload(?:s|ed|ing)?|load|semen|sperm)"
        r")",
        flags=re.IGNORECASE
    )
    m = MALE_TRIGGER.search(text)
    if m:
        # Check if it's "precum" and exclude it
        match_text = text[m.start():m.end()].lower()
        if 'precum' in match_text or 'pre-cum' in match_text:
            return (None, None)
        return (m.start(), m.end())
    return (None, None)

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
        
        # Parse commands from user input if they start with /
        if user_input.startswith('/'):
            parts = user_input.split(' ', 1)
            command = parts[0][1:]  # Remove the leading /
            if len(parts) > 1:
                # Add the rest as additional data
                data[command] = parts[1]
        
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
        # Initialize AI-powered story state manager
        if 'state_manager' not in session:
            session['state_manager'] = StoryStateManager()
        else:
            # Reset state for new story
            session['state_manager'].reset_state()
    
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
        # Handle /loadopener command - get filename from parsed command
        filename = data.get('loadopener', 'opener.txt')
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
            
            # Get AI-powered scene state reminder
            state_manager = session.get('state_manager')
            if state_manager:
                scene_state_reminder = state_manager.get_state_as_prompt()
            else:
                scene_state_reminder = """
CURRENT SCENE STATE (maintain this continuity):
- No characters tracked yet
- Location: unknown
- Positions: unknown
- Physical contact: none

Continue the story while maintaining this physical state. Do not have clothes magically reappear or positions change without explicit action.
"""
            
            # Add scene state reminder as a system message
            session['history'].append({"role": "system", "content": scene_state_reminder})
            
            # Generate AI response to continue the story
            try:
                print(f"🔍 Debug: Generating AI response for opener...")
                model_env = os.getenv('XAI_MODEL', 'grok-3')
                reply = chat_with_grok(
                    session['history'],
                    model=model_env,
                    temperature=0.9,
                    max_tokens=800,
                    top_p=0.8,
                    hide_thinking=True,
                )
                print(f"🔍 Debug: AI response generated, length={len(reply)}")
                
                # Add response to history
                session['history'].append({"role": "assistant", "content": reply})
                
                # Update scene state using AI-powered extraction
                try:
                    state_manager = session.get('state_manager')
                    if state_manager:
                        # Add the AI response to history for state extraction
                        temp_history = session['history'] + [{"role": "assistant", "content": reply}]
                        
                        # Use AI to intelligently extract current state
                        updated_state = state_manager.extract_state_from_messages(temp_history)
                        
                        print(f"🔍 Debug: AI-powered state extraction completed")
                        print(f"🔍 Debug: Current characters: {list(updated_state['characters'].keys())}")
                        for char_name, char_data in updated_state['characters'].items():
                            print(f"🔍 Debug: {char_name}: {char_data['clothing']}, {char_data['position']}, {char_data['mood']}")
                    else:
                        print(f"🔍 Debug: No state manager found in session")
                except Exception as e:
                    print(f"🔍 Debug: State extraction failed, continuing without update: {e}")
                
                return jsonify({
                    'message': f'📄 Loaded opener from {abs_path} (bytes={byte_len})',
                    'type': 'system',
                    'opener_content': opener,
                    'ai_response': reply,
                    'response_type': 'assistant'
                })
                
            except Exception as ai_error:
                print(f"🔍 Debug: AI response generation failed: {ai_error}")
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
    
    # Get AI-powered scene state reminder
    state_manager = session.get('state_manager')
    if state_manager:
        scene_state_reminder = state_manager.get_state_as_prompt()
    else:
        scene_state_reminder = """
CURRENT SCENE STATE (maintain this continuity):
- No characters tracked yet
- Location: unknown
- Positions: unknown
- Physical contact: none

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
        
                            # Limit history length for memory management
        if len(session['history']) > 12:  # Increased for paid tier
            print(f"🔍 Debug: Truncating history from {len(session['history'])} to 12 messages")
            # Keep system messages and last 8 messages
            system_messages = [m for m in session['history'] if m['role'] == 'system']
            recent_messages = session['history'][-8:]
            session['history'] = system_messages + recent_messages
            print(f"🔍 Debug: History truncated to {len(session['history'])} messages")
        
        # Try AI call with retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"🔍 Debug: Attempting AI call (attempt {attempt + 1}/{max_retries})...")
                reply = chat_with_grok(
                    session['history'],
                    model=model_env,
                    temperature=0.9,
                    max_tokens=min(800, session['max_tokens']),  # Increased for paid tier
                    top_p=0.8,
                    hide_thinking=True,
                )
                print(f"🔍 Debug: AI call successful, reply length={len(reply)}")
                break  # Success, exit retry loop
            except Exception as ai_error:
                print(f"🔍 Debug: AI call attempt {attempt + 1} failed: {ai_error}")
                print(f"🔍 Debug: Error type: {type(ai_error)}")
                
                if attempt == max_retries - 1:  # Last attempt
                    # Try with simpler request as final fallback
                    simple_history = [
                        {"role": "system", "content": "You are an explicit storyteller. Use detailed, sensual language."},
                        {"role": "user", "content": f"Continue this story: {user_input}"}
                    ]
                    print(f"🔍 Debug: Attempting final fallback AI call...")
                    try:
                        reply = chat_with_grok(
                            simple_history,
                            model=model_env,
                            temperature=0.7,
                            max_tokens=500,  # Increased for paid tier
                            top_p=0.7,
                            hide_thinking=True,
                        )
                        print(f"🔍 Debug: Fallback AI call successful, reply length={len(reply)}")
                    except Exception as fallback_error:
                        print(f"🔍 Debug: Fallback AI call also failed: {fallback_error}")
                        return jsonify({'error': f'AI service unavailable after {max_retries} attempts. Please try again.'})
                else:
                    # Wait a bit before retrying
                    import time
                    time.sleep(1)
        
        # Add response to history
        session['history'].append({"role": "assistant", "content": reply})
        
        # Update scene state using AI-powered extraction (with fallback)
        try:
            state_manager = session.get('state_manager')
            if state_manager:
                # Add the AI response to history for state extraction
                temp_history = session['history'] + [{"role": "assistant", "content": reply}]
                
                # Use AI to intelligently extract current state
                updated_state = state_manager.extract_state_from_messages(temp_history)
                
                print(f"🔍 Debug: AI-powered state extraction completed")
                print(f"🔍 Debug: Current characters: {list(updated_state['characters'].keys())}")
                for char_name, char_data in updated_state['characters'].items():
                    print(f"🔍 Debug: {char_name}: {char_data['clothing']}, {char_data['position']}, {char_data['mood']}")
            else:
                print(f"🔍 Debug: No state manager found in session")
        except Exception as e:
            print(f"🔍 Debug: State extraction failed, continuing without update: {e}")
            print(f"🔍 Debug: Error type: {type(e)}")
            # Continue without state update if extraction fails
        
        # Clean up session if it gets too large
        if len(session['history']) > 12:  # Increased for paid tier
            print(f"🔍 Debug: Session cleanup - history has {len(session['history'])} messages")
            # Keep system messages and last 6 messages
            system_messages = [m for m in session['history'] if m['role'] == 'system']
            recent_messages = session['history'][-6:]
            session['history'] = system_messages + recent_messages
            print(f"🔍 Debug: Session cleaned up to {len(session['history'])} messages")
        
        # Handle TTS if enabled
        audio_file = None
        if tts.enabled and reply.strip():
            try:
                # Generate TTS for responses (increased limit for paid tier)
                if len(reply) < 1200:  # Increased for paid tier
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

@app.route('/api/tts-toggle', methods=['POST'])
def toggle_tts():
    """Cycle through TTS modes: off -> tts -> save -> off"""
    try:
        data = request.get_json()
        action = data.get('action', 'cycle')
        
        if action == 'cycle':
            new_mode = tts.cycle_mode()
            mode_display = tts.get_mode_display()
            print(f"🔄 TTS mode cycled to: {mode_display}")
        elif action == 'enable':
            if tts.api_key:
                tts.mode = "tts"
                print(f"🎤 TTS enabled (auto-play)")
            else:
                return jsonify({'error': 'No TTS API key available'})
        elif action == 'disable':
            tts.mode = "off"
            print(f"🔇 TTS disabled")
        else:
            return jsonify({'error': f'Invalid action: {action}'})
        
        return jsonify({
            'success': True,
            'enabled': tts.enabled,
            'mode': tts.mode,
            'mode_display': tts.get_mode_display(),
            'message': f"TTS: {tts.get_mode_display()}"
        })
    except Exception as e:
        print(f"🔍 Debug: Error toggling TTS: {e}")
        return jsonify({'error': f'Failed to toggle TTS: {str(e)}'})

@app.route('/api/opener-files', methods=['GET'])
def get_opener_files():
    """Get list of available opener files"""
    try:
        import glob
        opener_files = glob.glob("opener*.txt")
        opener_files.sort()  # Sort alphabetically
        
        # Create a more user-friendly list with descriptions
        opener_list = []
        for filename in opener_files:
            # Extract description from filename
            name = filename.replace('.txt', '').replace('opener_', '').replace('_', ' ')
            if name == '2char':
                description = "2 characters - Generic office scenario"
            elif name == '3char':
                description = "3 characters - Emma, Alex & Jordan (threesome)"
            elif name == '4char':
                description = "4 characters - Rachel, Marcus, Sophia & David (foursome)"
            elif name == '5char':
                description = "5 characters - Isabella, James, Elena, Carlos & Maya (club VIP)"
            elif name == 'office 3':
                description = "3 characters - Jennifer, Mr. Thompson & Lisa (office)"
            elif name == 'party 4':
                description = "4 characters - Taylor, Chris, Ashley & Ryan (college party)"
            elif name == 'swingers':
                description = "4 characters - Michelle, Robert, Jessica & Michael (swingers)"
            elif name == 'bachelorette':
                description = "6 characters - Amanda, Brooke, Nicole, Vanessa, Tiffany & Destiny"
            elif name == 'fantasy':
                description = "6 characters - Aria, Thorne, Gimli, Pip, Grok & Zara (fantasy RPG)"
            elif name == 'sarah mike':
                description = "2 characters - Sarah & Mike (specific names)"
            else:
                description = f"{name} characters"
            
            opener_list.append({
                'filename': filename,
                'name': name.title(),
                'description': description
            })
        
        return jsonify({'opener_files': opener_list})
    except Exception as e:
        print(f"🔍 Debug: Error getting opener files: {e}")
        return jsonify({'error': f'Failed to get opener files: {str(e)}'})

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
        'mode': tts.mode,
        'mode_display': tts.get_mode_display(),
        'voice_id': tts.voice_id,
        'auto_save': tts.auto_save,
        'has_api_key': bool(tts.api_key)
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
