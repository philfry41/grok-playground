import os
import json
import re
import datetime
import gc
import signal
import atexit
import threading
import time
import hashlib
from flask import Flask, render_template, request, jsonify, session, send_from_directory
from grok_remote import chat_with_grok
from story_state_manager import StoryStateManager
from tts_helper import tts
import re
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "grok-playground-secret-key")

# Request deduplication tracking
active_requests = {}  # Track active requests to prevent duplicates
tts_generation_tracker = {}  # Track TTS generations to prevent duplicates

# Resource cleanup functions
def cleanup_resources():
    """Clean up resources to prevent memory leaks"""
    try:
        gc.collect()  # Force garbage collection
        print("🧹 Cleanup: Garbage collection completed")
    except Exception as e:
        print(f"⚠️ Cleanup error: {e}")

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    print(f"🛑 Received signal {signum}, cleaning up...")
    cleanup_resources()
    exit(0)

# Register cleanup handlers
atexit.register(cleanup_resources)
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

def generate_request_id(user_input, command=None):
    """Generate a unique request ID to prevent duplicates"""
    content = f"{user_input}:{command}:{session.get('session_id', 'no_session')}"
    return hashlib.md5(content.encode()).hexdigest()[:8]

def is_request_duplicate(request_id):
    """Check if this request is already being processed"""
    if request_id in active_requests:
        # Check if the request is still active (within 30 seconds)
        if time.time() - active_requests[request_id] < 30:
            return True
        else:
            # Remove stale request
            del active_requests[request_id]
    return False

def track_request(request_id):
    """Track an active request"""
    active_requests[request_id] = time.time()

def untrack_request(request_id):
    """Remove request from tracking"""
    if request_id in active_requests:
        del active_requests[request_id]

def generate_tts_async(text, save_audio=True, request_id=None):
    """Generate TTS audio in background thread with deduplication"""
    # Create a unique TTS generation ID that includes voice and content
    tts_id = hashlib.md5(f"{text[:100]}:{save_audio}:{tts.voice_id}:{request_id}".encode()).hexdigest()[:8]
    
    # Check if this TTS generation is already in progress
    if tts_id in tts_generation_tracker:
        print(f"🔍 Debug: TTS generation {tts_id} already in progress, skipping duplicate")
        return "generating"
    
    # Track this TTS generation with timeout
    tts_generation_tracker[tts_id] = time.time()
    
    # Set a timeout for TTS generation (2 minutes)
    TTS_TIMEOUT = 120
    
    def tts_worker():
        try:
            print(f"🔍 Debug: Starting async TTS generation {tts_id} for {len(text)} characters")
            start_time = time.time()
            
            # Always save audio files when TTS is enabled
            print(f"🔍 Debug: TTS mode - generating .mp3 file")
            print(f"🔍 Debug: Text to convert: {text[:100]}{'...' if len(text) > 100 else ''}")
            print(f"🔍 Debug: Text length: {len(text)} characters")
            audio_file = tts.speak(text, save_audio=True)
            
            end_time = time.time()
            duration = end_time - start_time
            
            if audio_file:
                print(f"🔍 Debug: Async TTS {tts_id} completed in {duration:.2f}s: {audio_file}")
                # Verify file exists after generation
                if os.path.exists(audio_file):
                    file_size = os.path.getsize(audio_file)
                    print(f"🔍 Debug: Async TTS {tts_id} file verified: {audio_file} ({file_size} bytes)")
                    
                    # Audio file ready for download/playback
                    print(f"🔍 Debug: Audio file ready: {audio_file}")
                    # The frontend will detect the new file via polling
                else:
                    print(f"🔍 Debug: Async TTS {tts_id} file missing after generation: {audio_file}")
            else:
                print(f"🔍 Debug: Async TTS {tts_id} failed after {duration:.2f}s")
                
        except Exception as e:
            print(f"🔍 Debug: Async TTS {tts_id} error: {e}")
            import traceback
            print(f"🔍 Debug: Async TTS {tts_id} error traceback: {traceback.format_exc()}")
        finally:
            # Remove from tracking when done
            if tts_id in tts_generation_tracker:
                del tts_generation_tracker[tts_id]
                print(f"🔍 Debug: TTS generation {tts_id} removed from tracking")
    
    # Start TTS generation in background thread with timeout
    thread = threading.Thread(target=tts_worker, daemon=True)
    thread.start()
    print(f"🔍 Debug: TTS generation {tts_id} started in background thread")
    
    # Set a timer to clean up if TTS takes too long
    def timeout_cleanup():
        time.sleep(TTS_TIMEOUT)
        if tts_id in tts_generation_tracker:
            print(f"🔍 Debug: TTS generation {tts_id} timed out after {TTS_TIMEOUT}s")
            del tts_generation_tracker[tts_id]
    
    timeout_thread = threading.Thread(target=timeout_cleanup, daemon=True)
    timeout_thread.start()
    
    return "generating"  # Return placeholder to indicate TTS is being generated

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
    print(f"🔍 Debug: Session ID: {session.get('_id', 'No session ID')}")
    print(f"🔍 Debug: Session keys: {list(session.keys())}")
    
    # Generate request ID for deduplication
    request_id = None
    
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
        
        # Generate request ID for deduplication
        request_id = generate_request_id(user_input, command)
        print(f"🔍 Debug: Request ID: {request_id}")
        
        # Check for duplicate requests
        if is_request_duplicate(request_id):
            print(f"🔍 Debug: Duplicate request detected: {request_id}")
            return jsonify({'error': 'Request already being processed. Please wait...'})
        
        # Track this request
        track_request(request_id)
        print(f"🔍 Debug: Request tracked: {request_id}")
        
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
        # Initialize AI-powered story state manager (not stored in session to avoid JSON serialization issues)
        if 'state_manager' not in session:
            # Create a new state manager but don't store it in session
            pass
    
    # Handle commands
    if command == 'new':
        session['history'] = session['history'][:2]
        if request_id:
            untrack_request(request_id)
        return jsonify({'message': '🧹 New scene. Priming kept.', 'type': 'system'})
    
    elif command == 'raw':
        session['history'].insert(0, {"role":"system","content":(
            "Reassert LEXICAL CONTRACT: use blunt, explicit anatomical language; avoid euphemisms; "
            "prioritize physical detail and explicit dialogue."
        )})
        if request_id:
            untrack_request(request_id)
        return jsonify({'message': '🎛️ Raw tone reasserted.', 'type': 'system'})
    
    elif command == 'edge':
        session['allow_female'], session['allow_male'] = True, False
        if request_id:
            untrack_request(request_id)
        return jsonify({'message': '⛓️ Edging: her allowed; his NOT.', 'type': 'system'})
    
    elif command == 'payoff':
        session['allow_female'], session['allow_male'] = True, True
        if request_id:
            untrack_request(request_id)
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
                    if request_id:
                        untrack_request(request_id)
                    return jsonify({'error': f'File encoding error: {str(e)}'})
                except PermissionError as e:
                    print(f"🔍 Debug: Permission error: {e}")
                    if request_id:
                        untrack_request(request_id)
                    return jsonify({'error': f'Permission denied reading {filename}: {str(e)}'})
                except Exception as e:
                    print(f"🔍 Debug: File read error: {e}")
                    if request_id:
                        untrack_request(request_id)
                    return jsonify({'error': f'Error reading {filename}: {str(e)}'})
            
            byte_len = len(opener.encode("utf-8"))
            if byte_len == 0 or not any(ch.strip() for ch in opener):
                if request_id:
                    untrack_request(request_id)
                return jsonify({'error': f'{filename} looks empty. Path: {abs_path} (bytes={byte_len})'})
            
            # Clear old history and add the opener content as a fresh start
            session['history'] = []  # Clear old history completely
            session['history'].append({"role": "user", "content": opener})
            print(f"🔍 Debug: Cleared old history and added opener content")
            
            # Get AI-powered scene state reminder (create locally to avoid session serialization issues)
            try:
                state_manager = StoryStateManager()
                scene_state_reminder = state_manager.get_state_as_prompt()
            except Exception as e:
                print(f"🔍 Debug: State manager error, using fallback: {e}")
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
            
            # Return opener content immediately, then handle TTS and AI response
            initial_response = {
                'message': f'📄 Loaded opener from {abs_path} (bytes={byte_len})',
                'type': 'system',
                'opener_content': opener,
                'ai_response': None,
                'response_type': 'system',
                'audio_file': None,
                'opener_audio_file': None
            }
            
            # Start TTS generation for opener text in background (if enabled)
            if tts.enabled and opener.strip():
                try:
                    print(f"🔍 Debug: Starting TTS for opener text, length={len(opener)}")
                    print(f"🔍 Debug: Using voice ID: {tts.voice_id}")
                    # Always save opener audio file when TTS is enabled (needed for both auto-play and auto-save)
                    save_audio = True
                    print(f"🔍 Debug: TTS for opener save_audio parameter: {save_audio}")
                    opener_audio_file = generate_tts_async(opener, save_audio=save_audio, request_id=request_id)
                    if opener_audio_file == "generating":
                        print(f"🔍 Debug: Async TTS started for opener text")
                        initial_response['opener_audio_file'] = "generating"
                except Exception as e:
                    print(f"🔍 Debug: TTS error for opener text: {e}")
                    import traceback
                    print(f"🔍 Debug: TTS error traceback for opener: {traceback.format_exc()}")
            else:
                print(f"🔍 Debug: TTS not enabled or opener empty for opener text")
            
            # Generate AI response to continue the story
            try:
                print(f"🔍 Debug: Generating AI response for opener...")
                model_env = os.getenv('XAI_MODEL', 'grok-3')
                # AI call for loadopener with proper continuity
                opener_context = [
                    {"role": "system", "content": "You are an explicit storyteller. Continue the story naturally from the opener text, maintaining the characters, setting, and situation described."},
                    {"role": "user", "content": f"Continue this story from where it left off:\n\n{opener}"}
                ]
                
                reply = chat_with_grok(
                    opener_context,
                    model=model_env,
                    temperature=0.7,
                    max_tokens=500,  # Slightly increased for better continuity
                    top_p=0.8,
                    hide_thinking=True,
                )
                print(f"🔍 Debug: AI response generated, length={len(reply)}")
                
                # Add response to history
                session['history'].append({"role": "assistant", "content": reply})
                print(f"🔍 Debug: After opener response - session history has {len(session['history'])} messages")
                for i, msg in enumerate(session['history']):
                    print(f"🔍 Debug: Opener history {i}: {msg['role']} - {msg['content'][:100]}...")
                
                # Handle TTS for AI response (same logic as main chat)
                audio_file = None
                print(f"🔍 Debug: TTS enabled: {tts.enabled}, mode: {tts.mode}")
                print(f"🔍 Debug: Reply length: {len(reply)}")
                
                if tts.enabled and reply.strip():
                    try:
                        # Handle TTS based on response length
                        if len(reply) < 1000:  # Short responses - generate TTS immediately
                            # Always save audio files (needed for both auto-play and auto-save)
                            save_audio = True
                            print(f"🔍 Debug: TTS save_audio parameter: {save_audio}")
                            print(f"🔍 Debug: Using voice ID for AI response: {tts.voice_id}")
                            audio_file = tts.speak(reply, save_audio=save_audio)
                        else:  # Long responses - generate TTS asynchronously
                            print(f"🔍 Debug: Long response ({len(reply)} chars) - using async TTS")
                            # Always save audio files (needed for both auto-play and auto-save)
                            save_audio = True
                            print(f"🔍 Debug: Using voice ID for AI response: {tts.voice_id}")
                            audio_file = generate_tts_async(reply, save_audio=save_audio, request_id=request_id)
                            if audio_file:
                                if audio_file == "generating":
                                    print(f"🔍 Debug: Async TTS started for AI response")
                                else:
                                    print(f"🔍 Debug: TTS generated for AI response: {audio_file}")
                            else:
                                print(f"🔍 Debug: TTS played for AI response (not saved)")
                        # TTS handled above based on length
                    except Exception as e:
                        print(f"🔍 Debug: TTS error for AI response: {e}")
                        import traceback
                        print(f"🔍 Debug: TTS error traceback: {traceback.format_exc()}")
                else:
                    print(f"🔍 Debug: TTS not enabled or reply empty for AI response")
                
                # Update scene state using AI-powered extraction (create locally to avoid session serialization issues)
                try:
                    state_manager = StoryStateManager()
                    # Add the AI response to history for state extraction
                    temp_history = session['history'] + [{"role": "assistant", "content": reply}]
                    
                    # Use AI to intelligently extract current state
                    updated_state = state_manager.extract_state_from_messages(temp_history)
                    
                    print(f"🔍 Debug: AI-powered state extraction completed")
                    print(f"🔍 Debug: Current characters: {list(updated_state['characters'].keys())}")
                    for char_name, char_data in updated_state['characters'].items():
                        print(f"🔍 Debug: {char_name}: {char_data['clothing']}, {char_data['position']}, {char_data['mood']}")
                except Exception as e:
                    print(f"🔍 Debug: State extraction failed, continuing without update: {e}")
                    print(f"🔍 Debug: Error type: {type(e)}")
                
                # Update initial response with AI response and audio
                initial_response['ai_response'] = reply
                initial_response['response_type'] = 'assistant'
                initial_response['audio_file'] = audio_file
                
                return jsonify(initial_response)
                
            except Exception as ai_error:
                print(f"🔍 Debug: AI response generation failed: {ai_error}")
                # Update initial response with fallback AI response
                initial_response['ai_response'] = 'Click "Send" to continue the story...'
                initial_response['response_type'] = 'system'
                
                return jsonify(initial_response)
                
        except FileNotFoundError:
            if request_id:
                untrack_request(request_id)
            return jsonify({'error': f'File not found: {filename}'})
        except Exception as e:
            if request_id:
                untrack_request(request_id)
            return jsonify({'error': f"Couldn't read {filename}: {e}"})
    
    elif command == 'cont':
        # Handle /cont command with full context for better story quality
        target = max(250, min(1000, word_count))  # Restored original range
        max_tokens = min(500, target)  # Restored original token limit
        
        # Create a simple continuation prompt
        user_input = f"Continue the story naturally. Write about {target} words."
        session['max_tokens'] = max_tokens
    
    # Robust session management to prevent cookie overflow
    if 'history' not in session:
        session['history'] = []
        print(f"🔍 Debug: Created new session history")
    
    print(f"🔍 Debug: Before adding user input - session history has {len(session['history'])} messages")
    
    # Keep only last 2 messages to prevent cookie overflow
    if len(session['history']) > 2:
        print(f"🔍 Debug: Truncating history from {len(session['history'])} to 2 messages to prevent cookie overflow")
        session['history'] = session['history'][-2:]
    
    # Add user message to history
    session['history'].append({"role": "user", "content": user_input})
    print(f"🔍 Debug: After adding user input - session history has {len(session['history'])} messages")
    
    try:
        # Get model from environment
        model_env = os.getenv("XAI_MODEL", "grok-3")
        api_key = os.getenv("XAI_API_KEY")
        
        print(f"🔍 Debug: Model: {model_env}")
        print(f"🔍 Debug: API Key set: {'Yes' if api_key else 'No'}")
        print(f"🔍 Debug: Starting AI call with {len(session['history'])} messages")
        
                            # Limit history length for memory management
        if len(session['history']) > 8:  # Reduced for Render stability
            print(f"🔍 Debug: Truncating history from {len(session['history'])} to 8 messages")
            # Keep system messages and last 6 messages
            system_messages = [m for m in session['history'] if m['role'] == 'system']
            recent_messages = session['history'][-6:]
            session['history'] = system_messages + recent_messages
            print(f"🔍 Debug: History truncated to {len(session['history'])} messages")
            
            # Force garbage collection after history cleanup
            cleanup_resources()
        
        # Try AI call with basic continuity for stability
        max_retries = 1  # Single attempt only
        try:
            print(f"🔍 Debug: Attempting AI call with continuity...")
            
            # Build context with recent history for continuity
            context_messages = []
            
            # Add system message with specific continuity instructions and scene state
            if command == 'cont':
                system_prompt = "You are an explicit storyteller. Continue the story naturally from where it left off. Maintain the exact same characters, location, and situation. Do not introduce new characters or change the setting unless explicitly mentioned in the previous context."
            else:
                # Get current scene state for continuity
                try:
                    state_manager = StoryStateManager()
                    scene_state_reminder = state_manager.get_state_as_prompt()
                    system_prompt = f"You are an explicit storyteller. {scene_state_reminder}"
                except Exception as e:
                    print(f"🔍 Debug: State manager error in main chat, using fallback: {e}")
                    system_prompt = "You are an explicit storyteller. Continue the story naturally."
            
            context_messages.append({"role": "system", "content": system_prompt})
            
            # Add recent history for continuity (with cookie overflow protection)
            if len(session['history']) > 0:
                print(f"🔍 Debug: Session history has {len(session['history'])} messages")
                for i, msg in enumerate(session['history']):
                    print(f"🔍 Debug: Message {i}: {msg['role']} - {msg['content'][:100]}...")
                
                # Use full history for better story continuity
                recent_history = session['history'][-3:]  # Use last 3 messages for all commands
                print(f"🔍 Debug: Using last {len(recent_history)} messages for continuity")
                context_messages.extend(recent_history)
            
            # Add current user input
            context_messages.append({"role": "user", "content": user_input})
            
            print(f"🔍 Debug: Using {len(context_messages)} messages for context")
            for i, msg in enumerate(context_messages):
                print(f"🔍 Debug: Context {i}: {msg['role']} - {msg['content'][:100]}...")
            
            # Use full tokens for better story quality
            max_tokens_for_call = 500 if command == 'cont' else 500
            
            print(f"🔍 Debug: About to call AI with {len(context_messages)} messages")
            print(f"🔍 Debug: Model: {model_env}")
            print(f"🔍 Debug: Max tokens: {max_tokens_for_call}")
            
            # Add timeout handling for /cont commands
            if command == 'cont':
                print(f"🔍 Debug: /cont command detected - allowing longer processing time")
                # Force cleanup before AI call
                cleanup_resources()
            
            reply = chat_with_grok(
                context_messages,
                model=model_env,
                temperature=0.7,
                max_tokens=max_tokens_for_call,
                top_p=0.8,
                hide_thinking=True,
            )
            
            print(f"🔍 Debug: AI response received, length: {len(reply)}")
            print(f"🔍 Debug: AI response starts with: {reply[:200]}...")
            
            print(f"🔍 Debug: AI call successful, reply length={len(reply)}")
        except Exception as ai_error:
            print(f"🔍 Debug: AI call failed: {ai_error}")
            print(f"🔍 Debug: Error type: {type(ai_error)}")
            
            # Force cleanup after failure
            cleanup_resources()
            
            # Return a simple fallback response
            reply = "I'm having trouble connecting right now. Please try again in a moment."
        
        # Add response to history with overflow protection
        session['history'].append({"role": "assistant", "content": reply})
        
        # Clean up session to prevent cookie overflow
        if len(session['history']) > 3:
            print(f"🔍 Debug: Cleaning up session history to prevent cookie overflow")
            session['history'] = session['history'][-3:]
        
        # Update scene state using AI-powered extraction (create locally to avoid session serialization issues)
        try:
            state_manager = StoryStateManager()
            # Add the AI response to history for state extraction
            temp_history = session['history'] + [{"role": "assistant", "content": reply}]
            
            # Use AI to intelligently extract current state
            updated_state = state_manager.extract_state_from_messages(temp_history)
            
            print(f"🔍 Debug: AI-powered state extraction completed")
            print(f"🔍 Debug: Current characters: {list(updated_state['characters'].keys())}")
            for char_name, char_data in updated_state['characters'].items():
                print(f"🔍 Debug: {char_name}: {char_data['clothing']}, {char_data['position']}, {char_data['mood']}")
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
        
        # Clean up before TTS to free memory
        cleanup_resources()
        
        if tts.enabled and reply.strip():
            try:
                # Generate TTS for responses (increased limit for paid tier)
                # Handle TTS based on response length
                if len(reply) < 2000:  # Increased threshold to reduce async TTS usage
                    # For auto-save mode, always save audio files
                    # For auto-play mode, don't save (just play)
                    save_audio = (tts.mode == "save")
                    print(f"🔍 Debug: Short response ({len(reply)} chars) - using immediate TTS")
                    audio_file = tts.speak(reply, save_audio=save_audio)
                else:  # Long responses - generate TTS asynchronously
                    print(f"🔍 Debug: Long response ({len(reply)} chars) - using async TTS")
                    save_audio = (tts.mode == "save")
                    audio_file = generate_tts_async(reply, save_audio=save_audio, request_id=request_id)
                    if audio_file:
                        if audio_file == "generating":
                            print(f"🔍 Debug: Async TTS started")
                        else:
                            print(f"🔍 Debug: TTS generated: {audio_file}")
                    else:
                        print(f"🔍 Debug: TTS played (not saved)")
            except Exception as e:
                print(f"🔍 Debug: TTS error: {e}")
        
        # Clean up request tracking before sending response
        if request_id:
            untrack_request(request_id)
            print(f"🔍 Debug: Request untracked: {request_id}")
        
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
        
        # Clean up request tracking on error
        if request_id:
            untrack_request(request_id)
            print(f"🔍 Debug: Request untracked on error: {request_id}")
        
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
                tts.mode = "save"
                tts._save_tts_mode("save")
                print(f"💾 TTS enabled (generate .mp3 files)")
            else:
                return jsonify({'error': 'No TTS API key available'})
        elif action == 'disable':
            tts.mode = "off"
            tts._save_tts_mode("off")
            print(f"🔇 TTS disabled")
        elif action == 'force_save':
            # Force save mode for testing
            tts.mode = "save"
            tts._save_tts_mode("save")
            print(f"💾 TTS forced to save mode")
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
        'has_api_key': bool(tts.api_key),
        'available_voices': tts.get_available_voices()
    })

@app.route('/api/tts-voice', methods=['POST'])
def set_tts_voice():
    """Set the TTS voice"""
    try:
        data = request.get_json()
        voice_id = data.get('voice_id')
        
        if not voice_id:
            return jsonify({'error': 'No voice_id provided'})
        
        # Set the voice
        tts.set_voice(voice_id)
        
        return jsonify({
            'success': True,
            'voice_id': voice_id,
            'message': f'Voice changed to: {voice_id}'
        })
    except Exception as e:
        print(f"🔍 Debug: Error setting TTS voice: {e}")
        return jsonify({'error': f'Failed to set TTS voice: {str(e)}'})

@app.route('/api/debug-info', methods=['GET'])
def get_debug_info():
    """Get debug information about the server state"""
    try:
        import traceback
        import sys
        
        debug_info = {
            'server_time': datetime.now().isoformat(),
            'python_version': sys.version,
            'environment_vars': {
                'XAI_API_KEY': 'Set' if os.getenv('XAI_API_KEY') else 'Not Set',
                'XAI_MODEL': os.getenv('XAI_MODEL', 'grok-3'),
                'FLASK_SECRET_KEY': 'Set' if os.getenv('FLASK_SECRET_KEY') else 'Not Set',
                'REQUEST_TIMEOUT': os.getenv('REQUEST_TIMEOUT', 'Not Set'),
                'WORKER_TIMEOUT': os.getenv('WORKER_TIMEOUT', 'Not Set')
            },
            'tts_status': {
                'enabled': tts.enabled,
                'mode': tts.mode,
                'api_key_set': bool(tts.api_key)
            },
            'file_system': {
                'current_dir': os.getcwd(),
                'files_in_dir': os.listdir('.')[:10],  # First 10 files
                'audio_dir_exists': os.path.exists('audio_files'),
                'tts_mode_file_exists': os.path.exists('tts_mode.txt')
            },
            'memory_info': {
                'gc_count': gc.get_count(),
                'gc_stats': gc.get_stats()
            }
        }
        
        return jsonify(debug_info)
    except Exception as e:
        print(f"🔍 Debug: Error getting debug info: {e}")
        import traceback
        return jsonify({
            'error': f'Failed to get debug info: {str(e)}',
            'traceback': traceback.format_exc()
        })

@app.route('/api/server-logs', methods=['GET'])
def get_server_logs():
    """Get recent server logs and error information"""
    try:
        import io
        import sys
        
        # Capture recent print statements (this is a simple approach)
        logs = []
        
        # Get recent error information
        logs.append(f"Server Time: {datetime.now().isoformat()}")
        logs.append(f"Python Version: {sys.version}")
        logs.append(f"Current Directory: {os.getcwd()}")
        logs.append(f"TTS Enabled: {tts.enabled}")
        logs.append(f"TTS Mode: {tts.mode}")
        logs.append(f"API Key Set: {'Yes' if os.getenv('XAI_API_KEY') else 'No'}")
        logs.append(f"Model: {os.getenv('XAI_MODEL', 'grok-3')}")
        logs.append("")
        
        # Check for common issues
        if not os.path.exists('audio_files'):
            logs.append("⚠️ Audio directory missing")
        if not os.path.exists('tts_mode.txt'):
            logs.append("⚠️ TTS mode file missing")
        if not tts.api_key:
            logs.append("⚠️ TTS API key not set")
        
        logs.append("")
        logs.append("Recent server activity would appear here...")
        logs.append("(Server logs are not captured in this simple implementation)")
        
        return jsonify({
            'logs': logs,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'error': f'Failed to get server logs: {str(e)}',
            'logs': [f"Error getting logs: {e}"]
        })

@app.route('/api/test-api', methods=['GET'])
def test_api():
    """Test endpoint to verify API key and basic connectivity"""
    try:
        api_key = os.getenv('XAI_API_KEY')
        model_env = os.getenv('XAI_MODEL', 'grok-3')
        
        print(f"🔍 Debug: Test API - Model: {model_env}")
        print(f"🔍 Debug: Test API - API Key set: {'Yes' if api_key else 'No'}")
        
        # Test file system access
        print(f"🔍 Debug: Test API - Testing file system...")
        test_file_info = {}
        
        try:
            # Test current directory
            current_dir = os.getcwd()
            test_file_info['current_dir'] = current_dir
            print(f"🔍 Debug: Test API - Current directory: {current_dir}")
            
            # Test if we can create a test file
            test_filename = "test_file_system.txt"
            test_content = f"Test file created at {datetime.now()}"
            
            with open(test_filename, "w") as f:
                f.write(test_content)
            test_file_info['test_file_created'] = True
            print(f"🔍 Debug: Test API - Test file created: {test_filename}")
            
            # Test if we can read the file back
            with open(test_filename, "r") as f:
                read_content = f.read()
            test_file_info['test_file_read'] = (read_content == test_content)
            print(f"🔍 Debug: Test API - Test file read: {test_file_info['test_file_read']}")
            
            # Test audio directory
            audio_dir = "audio"
            if os.path.exists(audio_dir):
                test_file_info['audio_dir_exists'] = True
                test_file_info['audio_dir_files'] = len([f for f in os.listdir(audio_dir) if f.endswith('.mp3')])
                print(f"🔍 Debug: Test API - Audio directory exists with {test_file_info['audio_dir_files']} MP3 files")
            else:
                test_file_info['audio_dir_exists'] = False
                print(f"🔍 Debug: Test API - Audio directory does not exist")
                
                # Try to create it
                try:
                    os.makedirs(audio_dir, exist_ok=True)
                    test_file_info['audio_dir_created'] = True
                    print(f"🔍 Debug: Test API - Audio directory created successfully")
                except Exception as create_error:
                    test_file_info['audio_dir_created'] = False
                    test_file_info['audio_dir_error'] = str(create_error)
                    print(f"🔍 Debug: Test API - Failed to create audio directory: {create_error}")
            
            # Clean up test file
            try:
                os.remove(test_filename)
                test_file_info['test_file_cleaned'] = True
                print(f"🔍 Debug: Test API - Test file cleaned up")
            except Exception as cleanup_error:
                test_file_info['test_file_cleaned'] = False
                test_file_info['cleanup_error'] = str(cleanup_error)
                print(f"🔍 Debug: Test API - Failed to clean up test file: {cleanup_error}")
                
        except Exception as fs_error:
            test_file_info['file_system_error'] = str(fs_error)
            print(f"🔍 Debug: Test API - File system test failed: {fs_error}")
        
        if not api_key:
            return jsonify({
                'error': 'XAI_API_KEY not set',
                'file_system_test': test_file_info
            })
        
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
                'model': model_env,
                'file_system_test': test_file_info
            })
        except Exception as api_error:
            print(f"🔍 Debug: Test API - Error: {api_error}")
            print(f"🔍 Debug: Test API - Error type: {type(api_error)}")
            return jsonify({
                'success': False,
                'error': str(api_error),
                'api_key_set': True,
                'model': model_env,
                'file_system_test': test_file_info
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
        
        print(f"🔍 Debug: Serving audio file: {filename}")
        print(f"🔍 Debug: File path: {file_path}")
        print(f"🔍 Debug: File exists: {os.path.exists(file_path)}")
        
        if os.path.exists(file_path) and os.path.isfile(file_path):
            file_size = os.path.getsize(file_path)
            print(f"🔍 Debug: File size: {file_size} bytes")
            
            # Set proper headers for audio files
            response = send_from_directory(audio_dir, filename, as_attachment=False)
            response.headers['Content-Type'] = 'audio/mpeg'
            response.headers['Accept-Ranges'] = 'bytes'
            response.headers['Cache-Control'] = 'no-cache'
            
            print(f"🔍 Debug: Audio file served successfully")
            return response
        else:
            print(f"🔍 Debug: Audio file not found: {file_path}")
            return jsonify({'error': 'Audio file not found'}), 404
    except Exception as e:
        print(f"🔍 Debug: Error serving audio file: {e}")
        return jsonify({'error': f'Could not serve audio file: {e}'}), 500

@app.route('/api/audio-files', methods=['GET'])
def list_audio_files():
    """List all available audio files"""
    try:
        audio_dir = "audio"
        print(f"🔍 Debug: Checking audio directory: {audio_dir}")
        print(f"🔍 Debug: Current working directory: {os.getcwd()}")
        print(f"🔍 Debug: Audio directory exists: {os.path.exists(audio_dir)}")
        
        if not os.path.exists(audio_dir):
            print(f"🔍 Debug: Audio directory does not exist, creating it")
            try:
                os.makedirs(audio_dir, exist_ok=True)
                print(f"🔍 Debug: Audio directory created successfully")
            except Exception as create_error:
                print(f"🔍 Debug: Failed to create audio directory: {create_error}")
                return jsonify({'error': f'Could not create audio directory: {create_error}'}), 500
        
        files = []
        try:
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
                    print(f"🔍 Debug: Found audio file: {filename} ({file_size} bytes)")
        except Exception as list_error:
            print(f"🔍 Debug: Error listing audio files: {list_error}")
            return jsonify({'error': f'Could not list audio files: {list_error}'}), 500
        
        # Sort by creation time (newest first)
        files.sort(key=lambda x: x['created'], reverse=True)
        print(f"🔍 Debug: Total audio files found: {len(files)}")
        
        # List all files for debugging
        for i, file in enumerate(files[:5]):  # Show first 5 files
            print(f"🔍 Debug: File {i+1}: {file['filename']} ({file['size']} bytes)")
        
        return jsonify({'files': files})
    except Exception as e:
        print(f"🔍 Debug: Unexpected error in list_audio_files: {e}")
        return jsonify({'error': f'Could not list audio files: {e}'}), 500

if __name__ == '__main__':
    # Set timeout for requests to prevent hung processes
    import signal
    
    def timeout_handler(signum, frame):
        print("⏰ Request timeout - cleaning up...")
        cleanup_resources()
        raise TimeoutError("Request timed out")
    
        # Set 5-minute timeout for requests
    signal.signal(signal.SIGALRM, timeout_handler)
    
    port = int(os.environ.get('PORT', 8080))
    print(f"🎭 Starting Grok Playground Web Interface on port {port}")
    print(f"📍 Local: http://localhost:{port}")
    print(f"🌐 Network: http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
