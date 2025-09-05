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
from flask import Flask, render_template, request, jsonify, session, send_from_directory, redirect, url_for
from grok_remote import chat_with_grok
from story_state_manager import StoryStateManager
from tts_helper import tts
import re
from datetime import datetime

# Try to import database packages, but don't fail if they're not available
try:
    from flask_sqlalchemy import SQLAlchemy
    from flask_migrate import Migrate
    DATABASE_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ Database packages not available: {e}")
    DATABASE_AVAILABLE = False
    SQLAlchemy = None
    Migrate = None

# Try to import OAuth packages
try:
    from authlib.integrations.flask_client import OAuth
    OAUTH_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ OAuth packages not available: {e}")
    OAUTH_AVAILABLE = False
    OAuth = None

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "grok-playground-secret-key")

# Database configuration (only if database packages are available)
if DATABASE_AVAILABLE:
    DATABASE_URL = os.getenv('DATABASE_URL')
    if DATABASE_URL:
        # Handle PostgreSQL URL format for SQLAlchemy
        if DATABASE_URL.startswith('postgres://'):
            DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    else:
        # Fallback to SQLite for local development
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///grok_playground.db'

    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Initialize database
    db = SQLAlchemy(app)
    migrate = Migrate(app, db)
    print("✅ Database initialized successfully")
    
    # Initialize database tables immediately
    try:
        with app.app_context():
            db.create_all()
            print("✅ Database tables created successfully")
    except Exception as e:
        print(f"❌ Database table creation failed: {e}")
        import traceback
        print(f"Database error traceback: {traceback.format_exc()}")
else:
    print("⚠️ Database not available - running without database features")
    db = None
    migrate = None

# OAuth configuration (only if OAuth packages are available)
if OAUTH_AVAILABLE:
    # Google OAuth configuration
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET')
    
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        # Create OAuth client
        oauth = OAuth(app)
        google = oauth.register(
            name='google',
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={
                'scope': 'openid email profile'
            }
        )
        print("✅ Google OAuth configured successfully")
    else:
        print("⚠️ Google OAuth credentials not found in environment variables")
        oauth = None
        google = None
else:
    print("⚠️ OAuth not available - running without authentication features")
    oauth = None
    google = None

# Database Models (only if database is available)
if DATABASE_AVAILABLE:
    class User(db.Model):
        """User model for authentication and story ownership"""
        __tablename__ = 'users'
        
        id = db.Column(db.Integer, primary_key=True)
        google_id = db.Column(db.String(120), unique=True, nullable=False)
        email = db.Column(db.String(120), unique=True, nullable=False)
        name = db.Column(db.String(120), nullable=False)
        avatar_url = db.Column(db.String(200))
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        
        # Relationship to stories
        stories = db.relationship('Story', backref='owner', lazy=True)
        
        def __repr__(self):
            return f'<User {self.name} ({self.email})>'

    class Story(db.Model):
        """Story model for storing story data"""
        __tablename__ = 'stories'
        
        id = db.Column(db.Integer, primary_key=True)
        story_id = db.Column(db.String(80), unique=True, nullable=False)
        title = db.Column(db.String(200), nullable=False)
        user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
        content = db.Column(db.JSON, nullable=False)  # Store story data as JSON
        is_public = db.Column(db.Boolean, default=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
        
        def __repr__(self):
            return f'<Story {self.title} ({self.story_id})>'
else:
    # Dummy classes when database is not available
    class User:
        pass
    class Story:
        pass

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
            
            # Ensure voice ID is loaded fresh from file before generating TTS
            print(f"🔍 Debug: Ensuring voice ID is loaded from file before async TTS generation")
            tts.voice_id = tts._load_voice_id()
            print(f"🔍 Debug: Using voice ID for async TTS: {tts.voice_id}")
            
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

# Conversation persistence
CONVERSATIONS_DIR = "conversations"

def ensure_conversations_dir():
    """Ensure the conversations directory exists"""
    if not os.path.exists(CONVERSATIONS_DIR):
        os.makedirs(CONVERSATIONS_DIR)
        print(f"🔍 Debug: Created conversations directory: {CONVERSATIONS_DIR}")

def get_conversation_filename(story_id=None):
    """Generate filename for conversation history"""
    if story_id:
        return f"conversation_{story_id}_{datetime.now().strftime('%Y%m%d')}.json"
    else:
        return f"conversation_general_{datetime.now().strftime('%Y%m%d')}.json"

def save_conversation_history(history, story_id=None, user_input=None, ai_response=None):
    """Save conversation history to file"""
    try:
        ensure_conversations_dir()
        
        # Add current interaction if provided
        current_conversation = history.copy()
        if user_input:
            current_conversation.append({"role": "user", "content": user_input, "timestamp": datetime.now().isoformat()})
        if ai_response:
            current_conversation.append({"role": "assistant", "content": ai_response, "timestamp": datetime.now().isoformat()})
        
        # Create conversation data
        conversation_data = {
            "story_id": story_id,
            "last_updated": datetime.now().isoformat(),
            "message_count": len(current_conversation),
            "history": current_conversation
        }
        
        # Save to file
        filename = get_conversation_filename(story_id)
        filepath = os.path.join(CONVERSATIONS_DIR, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(conversation_data, f, indent=2, ensure_ascii=False)
        
        print(f"🔍 Debug: Saved conversation history to {filepath} ({len(current_conversation)} messages)")
        return True
        
    except Exception as e:
        print(f"🔍 Debug: Error saving conversation history: {e}")
        return False

def load_conversation_history(story_id=None):
    """Load conversation history from file"""
    try:
        ensure_conversations_dir()
        
        # Look for the most recent conversation file for this story
        if story_id:
            pattern = f"conversation_{story_id}_*.json"
        else:
            pattern = "conversation_general_*.json"
        
        import glob
        files = glob.glob(os.path.join(CONVERSATIONS_DIR, pattern))
        
        if not files:
            print(f"🔍 Debug: No conversation history found for story_id: {story_id}")
            return []
        
        # Get the most recent file
        latest_file = max(files, key=os.path.getctime)
        
        with open(latest_file, 'r', encoding='utf-8') as f:
            conversation_data = json.load(f)
        
        history = conversation_data.get('history', [])
        print(f"🔍 Debug: Loaded conversation history from {latest_file} ({len(history)} messages)")
        return history
        
    except Exception as e:
        print(f"🔍 Debug: Error loading conversation history: {e}")
        return []

def get_current_story_id():
    """Extract current story ID from session history"""
    try:
        if 'history' in session:
            for msg in session['history']:
                if msg.get('role') == 'system' and 'CHARACTERS:' in msg.get('content', ''):
                    # This is likely a story system prompt, extract story ID
                    if 'story_id' in session:
                        return session.get('story_id')
                    # Try to extract from the first system message
                    if session['history'] and 'story_id' in session['history'][0]:
                        return session['history'][0].get('story_id')
        return None
    except Exception as e:
        print(f"🔍 Debug: Error getting current story ID: {e}")
        return None

def extract_key_story_points(history):
    """Extract key plot points and story milestones from conversation history"""
    try:
        key_points = []
        
        # Look for story milestones in conversation
        for msg in history:
            if msg['role'] == 'assistant':
                content = msg['content'].lower()
                
                # Extract key character developments
                if any(phrase in content for phrase in ['first time', 'never done', 'never tried']):
                    key_points.append("Character experiencing new sexual activity")
                
                # Extract relationship developments
                if 'phil' in content and any(phrase in content for phrase in ['support', 'watched', 'encouraged']):
                    key_points.append("Phil supports Stephanie's sexual exploration")
                
                # Extract location developments
                if any(location in content for location in ['lake', 'cabin', 'boat', 'kitchen', 'bedroom']):
                    key_points.append(f"Story location: {extract_location_from_content(content)}")
                
                # Extract sexual encounters
                if any(phrase in content for phrase in ['sucked', 'fucked', 'came', 'climaxed', 'orgasm']):
                    key_points.append("Sexual encounter occurred")
                
                # Extract character growth
                if any(phrase in content for phrase in ['confidence', 'aroused', 'desire', 'hungry']):
                    key_points.append("Character sexual confidence growing")
                
                # Extract emotional developments
                if any(phrase in content for phrase in ['guilt', 'shame', 'liberated', 'free']):
                    key_points.append("Character emotional transformation")
        
        # Remove duplicates while preserving order
        seen = set()
        unique_points = []
        for point in key_points:
            if point not in seen:
                seen.add(point)
                unique_points.append(point)
        
        print(f"🔍 Debug: Extracted {len(unique_points)} key story points")
        return unique_points[:5]  # Limit to 5 most important points
        
    except Exception as e:
        print(f"🔍 Debug: Error extracting key story points: {e}")
        return []

def extract_location_from_content(content):
    """Extract specific location from content"""
    if 'lake' in content and 'cabin' in content:
        return "Lake cabin"
    elif 'boat' in content:
        return "Boat"
    elif 'kitchen' in content:
        return "Kitchen"
    elif 'bedroom' in content:
        return "Bedroom"
    elif 'farm' in content:
        return "Farm"
    else:
        return "Various locations"

def get_core_story_context(story_id):
    """Get compressed core story context that should always be included"""
    try:
        if not story_id:
            return None
            
        story_filename = f"story_{story_id}.json"
        if not os.path.exists(story_filename):
            return None
            
        with open(story_filename, "r", encoding="utf-8") as f:
            story_data = json.load(f)
        
        # Build compressed core context
        core_parts = []
        
        # Characters (compressed)
        if story_data.get('characters'):
            char_summaries = []
            for char_key, char_data in story_data['characters'].items():
                name = char_data.get('name', 'Unknown')
                age = char_data.get('age', 'unknown age')
                role = char_data.get('role', '')
                arc = char_data.get('sexual_growth_arc', '')
                
                char_summary = f"{name} ({age})"
                if role:
                    char_summary += f" - {role}"
                if arc:
                    char_summary += f" - {arc}"
                
                char_summaries.append(char_summary)
            
            core_parts.append(f"CHARACTERS:\n" + "\n".join(char_summaries))
        
        # Setting (compressed)
        if story_data.get('setting'):
            setting = story_data['setting']
            setting_summary = f"SETTING: {setting.get('location', 'Unknown')}"
            if setting.get('time'):
                setting_summary += f" | Time: {setting['time']}"
            if setting.get('atmosphere'):
                setting_summary += f" | Mood: {setting['atmosphere']}"
            core_parts.append(setting_summary)
        
        # Narrative guidelines (compressed)
        if story_data.get('narrative_guidelines'):
            guidelines = story_data['narrative_guidelines']
            
            if guidelines.get('lexical_contract'):
                contract = guidelines['lexical_contract']
                required = contract.get('required', [])
                forbidden = contract.get('forbidden', [])
                
                if required:
                    core_parts.append(f"REQUIRED VOCABULARY: {', '.join(required[:5])}")  # Limit to 5
                if forbidden:
                    core_parts.append(f"FORBIDDEN TERMS: {', '.join(forbidden[:5])}")  # Limit to 5
            
            if guidelines.get('tone'):
                core_parts.append(f"TONE: {guidelines['tone']}")
            
            if guidelines.get('pacing'):
                core_parts.append(f"PACING: {guidelines['pacing']}")
        
        core_context = "\n".join(core_parts)
        print(f"🔍 Debug: Core story context length: {len(core_context)} chars")
        
        return core_context
        
    except Exception as e:
        print(f"🔍 Debug: Error getting core story context: {e}")
        return None

def require_auth(f):
    """Decorator to require authentication for protected routes"""
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'error': 'Authentication required', 'login_url': '/auth/google'}), 401
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

@app.route('/')
def index():
    # Check if user is logged in
    if not session.get('logged_in'):
        return render_template('login_required.html')
    return render_template('index.html')

@app.route('/api/oauth-test')
def oauth_test():
    """Test endpoint to check OAuth availability"""
    return jsonify({
        'oauth_available': OAUTH_AVAILABLE,
        'google_available': google is not None if OAUTH_AVAILABLE else False,
        'client_id_set': bool(os.getenv('GOOGLE_OAUTH_CLIENT_ID')),
        'client_secret_set': bool(os.getenv('GOOGLE_OAUTH_CLIENT_SECRET'))
    })

# OAuth routes (only if OAuth is available)
if OAUTH_AVAILABLE:
    @app.route('/auth/google')
    def google_login():
        """Initiate Google OAuth login"""
        try:
            redirect_uri = 'https://grok-playground.onrender.com/auth/google/callback'
            return google.authorize_redirect(redirect_uri)
        except Exception as e:
            print(f"🔍 Debug: Google login error: {e}")
            return jsonify({'error': f'Google login failed: {str(e)}'}), 500

    @app.route('/auth/google/callback')
    def google_callback():
        """Handle Google OAuth callback"""
        try:
            # Get the authorization code from the callback
            token = google.authorize_access_token()
            user_info = token.get('userinfo')
            
            if not user_info:
                return jsonify({'error': 'Failed to get user info from Google'}), 400
            
            print(f"🔍 Debug: Google user info: {user_info}")
            
            # Extract user data
            google_id = user_info.get('sub')
            email = user_info.get('email')
            name = user_info.get('name')
            avatar_url = user_info.get('picture')
            
            if not google_id or not email:
                return jsonify({'error': 'Invalid user data from Google'}), 400
            
            # Store user info in session
            session['user_id'] = google_id
            session['user_email'] = email
            session['user_name'] = name
            session['user_avatar'] = avatar_url
            session['logged_in'] = True
            
            # Save user to database if available
            if DATABASE_AVAILABLE:
                try:
                    # Check if user exists
                    user = User.query.filter_by(google_id=google_id).first()
                    
                    if not user:
                        # Create new user
                        user = User(
                            google_id=google_id,
                            email=email,
                            name=name,
                            avatar_url=avatar_url
                        )
                        db.session.add(user)
                        db.session.commit()
                        print(f"🔍 Debug: Created new user: {name} ({email}) with ID: {user.id}")
                    else:
                        # Update existing user
                        user.email = email
                        user.name = name
                        user.avatar_url = avatar_url
                        db.session.commit()
                        print(f"🔍 Debug: Updated existing user: {name} ({email}) with ID: {user.id}")
                    
                    session['db_user_id'] = user.id
                    print(f"🔍 Debug: Set session db_user_id to: {user.id}")
                    
                except Exception as db_error:
                    print(f"🔍 Debug: Database error during user save: {db_error}")
                    import traceback
                    print(f"🔍 Debug: Database error traceback: {traceback.format_exc()}")
                    # Continue without database save
                    session['db_user_id'] = None
            else:
                print("🔍 Debug: Database not available, setting db_user_id to None")
                session['db_user_id'] = None
            
            print(f"🔍 Debug: User logged in successfully: {name} ({email})")
            
            # Redirect to main page
            return redirect('/')
            
        except Exception as e:
            print(f"🔍 Debug: OAuth callback error: {e}")
            return jsonify({'error': f'OAuth callback failed: {str(e)}'}), 500
    
    @app.route('/auth/logout')
    def logout():
        """Logout user"""
        try:
            # Clear session
            session.clear()
            print("🔍 Debug: User logged out")
            
            return jsonify({
                'success': True,
                'message': 'Logged out successfully'
            })
            
        except Exception as e:
            print(f"🔍 Debug: Logout error: {e}")
            return jsonify({'error': f'Logout failed: {str(e)}'}), 500
    
    @app.route('/auth/status')
    def auth_status():
        """Get current authentication status"""
        try:
            if session.get('logged_in'):
                return jsonify({
                    'logged_in': True,
                    'user': {
                        'name': session.get('user_name'),
                        'email': session.get('user_email'),
                        'avatar': session.get('user_avatar')
                    }
                })
            else:
                return jsonify({
                    'logged_in': False,
                    'user': None
                })
                
        except Exception as e:
            print(f"🔍 Debug: Auth status error: {e}")
            return jsonify({'error': f'Auth status check failed: {str(e)}'}), 500

@app.route('/api/chat', methods=['POST'])
@require_auth
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
            
            # TTS will be generated on-demand via button, not automatically for opener text
            print(f"🔍 Debug: TTS enabled: {tts.enabled}")
            print(f"🔍 Debug: Opener text length: {len(opener)}")
            print(f"🔍 Debug: TTS will be generated on-demand when user clicks 'Play TTS' button")
            
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
                
                # TTS will be generated on-demand via button, not automatically
                print(f"🔍 Debug: TTS enabled: {tts.enabled}")
                print(f"🔍 Debug: Reply length: {len(reply)}")
                print(f"🔍 Debug: TTS will be generated on-demand when user clicks 'Play TTS' button")
                
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
                
                # Update initial response with AI response (no audio file yet)
                initial_response['ai_response'] = reply
                initial_response['response_type'] = 'assistant'
                initial_response['audio_file'] = None  # Will be generated on-demand
                
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
    
    elif command == 'loadstory':
        print(f"🔍 Debug: loadstory command detected")
        # Handle /loadstory command - get story ID from parsed command
        story_id = data.get('loadstory', 'farm_romance')
        print(f"🔍 Debug: story_id='{story_id}'")
        print(f"🔍 Debug: data keys: {list(data.keys())}")
        print(f"🔍 Debug: user_input: {user_input}")
        
        try:
            # Get current user from session
            user_id = session.get('db_user_id')
            google_id = session.get('user_id')  # Fallback to Google ID
            
            if not user_id and not google_id:
                if request_id:
                    untrack_request(request_id)
                return jsonify({'error': 'User not found in session'})
            
            # Use Google ID as fallback if database user ID not available
            if not user_id:
                print(f"🔍 Debug: Using Google ID as fallback: {google_id}")
                user_id = google_id
            
            # Load story from database
            if DATABASE_AVAILABLE:
                story = Story.query.filter_by(story_id=story_id, user_id=user_id).first()
                
                if not story:
                    if request_id:
                        untrack_request(request_id)
                    return jsonify({'error': f'Story not found: {story_id}'})
                
                story_data = story.content
                print(f"🔍 Debug: Loaded story from database: {story.title}")
            else:
                # Fallback to file system if database not available
                story_filename = f"story_{story_id}.json"
                story_path = os.path.abspath(story_filename)
                print(f"🔍 Debug: story_path='{story_path}'")
                
                if not os.path.exists(story_filename):
                    if request_id:
                        untrack_request(request_id)
                    return jsonify({'error': f'Story file not found: {story_filename}'})
                
                # Read and parse the story JSON
                with open(story_filename, "r", encoding="utf-8") as f:
                    story_data = json.load(f)
            
            print(f"🔍 Debug: Loaded story: {story_data.get('title', story_id)}")
            print(f"🔍 Debug: Story data keys: {list(story_data.keys())}")
            print(f"🔍 Debug: Story data type: {type(story_data)}")
            print(f"🔍 Debug: Story data content preview: {str(story_data)[:200]}...")
            
            # Extract story components (data is flat, not nested under 'story' key)
            opener_text = story_data.get('opener_text', '')
            characters = story_data.get('characters', {})
            setting = story_data.get('setting', {})
            narrative_guidelines = story_data.get('narrative_guidelines', {})
            
            print(f"🔍 Debug: Story data structure - story keys: {list(story_data.keys())}")
            print(f"🔍 Debug: Opener text length: {len(opener_text)}")
            print(f"🔍 Debug: Characters count: {len(characters)}")
            print(f"🔍 Debug: Setting keys: {list(setting.keys())}")
            print(f"🔍 Debug: Narrative guidelines keys: {list(narrative_guidelines.keys())}")
            
            # Build comprehensive system prompt from story data
            system_prompt_parts = []
            
            # Add character information
            if characters:
                char_info = []
                for char_key, char_data in characters.items():
                    char_name = char_data.get('name', 'Unknown')
                    char_age = char_data.get('age', 'unknown age')
                    char_occupation = char_data.get('occupation', '')
                    char_physical = char_data.get('physical', {})
                    char_personality = char_data.get('personality', {})
                    
                    char_desc = f"{char_name} ({char_age})"
                    if char_occupation:
                        char_desc += f", {char_occupation}"
                    
                    # Add physical description
                    if char_physical:
                        physical_parts = []
                        if char_physical.get('height'): physical_parts.append(char_physical['height'])
                        if char_physical.get('build'): physical_parts.append(char_physical['build'])
                        if char_physical.get('hair'): physical_parts.append(char_physical['hair'])
                        if char_physical.get('eyes'): physical_parts.append(char_physical['eyes'])
                        if physical_parts:
                            char_desc += f" - {', '.join(physical_parts)}"
                    
                    # Add personality traits
                    if char_personality:
                        traits = char_personality.get('traits', [])
                        if traits:
                            char_desc += f" - {', '.join(traits)}"
                    
                    char_info.append(char_desc)
                
                system_prompt_parts.append(f"CHARACTERS:\n" + "\n".join(char_info))
            
            # Add setting information
            if setting:
                setting_parts = []
                if setting.get('location'): setting_parts.append(f"Location: {setting['location']}")
                if setting.get('time'): setting_parts.append(f"Time: {setting['time']}")
                if setting.get('atmosphere'): setting_parts.append(f"Atmosphere: {setting['atmosphere']}")
                if setting_parts:
                    system_prompt_parts.append("SETTING:\n" + "\n".join(setting_parts))
            
            # Add narrative guidelines
            if narrative_guidelines:
                if narrative_guidelines.get('lexical_contract'):
                    contract = narrative_guidelines['lexical_contract']
                    required = contract.get('required', [])
                    forbidden = contract.get('forbidden', [])
                    
                    if required:
                        system_prompt_parts.append(f"REQUIRED VOCABULARY: {', '.join(required)}")
                    if forbidden:
                        system_prompt_parts.append(f"FORBIDDEN TERMS: {', '.join(forbidden)}")
                
                if narrative_guidelines.get('tone'):
                    system_prompt_parts.append(f"TONE: {narrative_guidelines['tone']}")
                
                if narrative_guidelines.get('pacing'):
                    system_prompt_parts.append(f"PACING: {narrative_guidelines['pacing']}")
            
            # Combine all parts
            comprehensive_system_prompt = "\n\n".join(system_prompt_parts)
            print(f"🔍 Debug: Comprehensive system prompt length: {len(comprehensive_system_prompt)}")
            
            # Store story ID in session for persistence
            session['story_id'] = story_id
            
            # Try to load existing conversation history for this story
            existing_history = load_conversation_history(story_id)
            
            if existing_history:
                # Use existing conversation history
                session['history'] = existing_history
                print(f"🔍 Debug: Loaded existing conversation history ({len(existing_history)} messages)")
            else:
                # Start fresh with the story setup
                session['history'] = []
                
                # Add comprehensive system prompt
                if comprehensive_system_prompt:
                    session['history'].append({"role": "system", "content": comprehensive_system_prompt})
                
                # Add opener text as user message
                if opener_text:
                    session['history'].append({"role": "user", "content": opener_text})
                
                print(f"🔍 Debug: Started fresh conversation history")
            
            print(f"🔍 Debug: Session history now has {len(session['history'])} messages")
            
            # Return story content immediately
            initial_response = {
                'message': f'📚 Loaded story: {story_data.get("title", story_id)}',
                'type': 'system',
                'opener_content': opener_text,
                'ai_response': None,
                'response_type': 'system',
                'audio_file': None,
                'opener_audio_file': None
            }
            
            # Generate AI response to continue the story
            try:
                print(f"🔍 Debug: Generating AI response for story...")
                model_env = os.getenv('XAI_MODEL', 'grok-3')
                
                # Build context for AI call
                context_messages = []
                
                # Add comprehensive system prompt
                if comprehensive_system_prompt:
                    context_messages.append({"role": "system", "content": f"You are an explicit storyteller. {comprehensive_system_prompt}"})
                    print(f"🔍 Debug: Added comprehensive system prompt to AI context")
                else:
                    context_messages.append({"role": "system", "content": "You are an explicit storyteller. Continue the story naturally from the opener text."})
                    print(f"🔍 Debug: Added fallback system prompt to AI context")
                
                # Add opener text
                if opener_text:
                    context_messages.append({"role": "user", "content": f"Continue this story from where it left off:\n\n{opener_text}"})
                    print(f"🔍 Debug: Added opener text to AI context")
                
                print(f"🔍 Debug: AI context messages count: {len(context_messages)}")
                for i, msg in enumerate(context_messages):
                    print(f"🔍 Debug: AI context {i}: {msg['role']} - {msg['content'][:200]}...")
                
                reply = chat_with_grok(
                    context_messages,
                    model=model_env,
                    temperature=0.7,
                    max_tokens=session.get('max_tokens', 1200)
                )
                
                if reply and reply.strip():
                    initial_response['ai_response'] = reply
                    initial_response['response_type'] = 'assistant'
                    
                    # Add AI response to session history
                    session['history'].append({"role": "assistant", "content": reply})
                    
                    # Update scene state with AI response
                    try:
                        state_manager = StoryStateManager()
                        state_manager.update_state_from_response(reply)
                        print(f"🔍 Debug: Updated scene state from AI response")
                    except Exception as e:
                        print(f"🔍 Debug: State manager error: {e}")
                    
                    # Save conversation history for persistence
                    save_conversation_history(session['history'], story_id, None, reply)
                    
                    print(f"🔍 Debug: AI response generated, length={len(reply)}")
                else:
                    initial_response['ai_response'] = 'Click "Send" to continue the story...'
                    initial_response['response_type'] = 'system'
                
                if request_id:
                    untrack_request(request_id)
                return jsonify(initial_response)
                
            except Exception as ai_error:
                print(f"🔍 Debug: AI response generation failed: {ai_error}")
                initial_response['ai_response'] = 'Click "Send" to continue the story...'
                initial_response['response_type'] = 'system'
                
                if request_id:
                    untrack_request(request_id)
                return jsonify(initial_response)
                
        except FileNotFoundError:
            if request_id:
                untrack_request(request_id)
            return jsonify({'error': f'Story file not found: {story_filename}'})
        except json.JSONDecodeError as e:
            if request_id:
                untrack_request(request_id)
            return jsonify({'error': f'Invalid JSON in story file: {str(e)}'})
        except Exception as e:
            if request_id:
                untrack_request(request_id)
            return jsonify({'error': f"Couldn't load story: {str(e)}"})
    
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
            
            # Build context with hybrid approach: core story context + scene state + key memories + recent history
            context_messages = []
            
            # 1. Core system prompt
            context_messages.append({
                "role": "system", 
                "content": "You are an explicit storyteller. Maintain story continuity."
            })
            
            # 2. CORE STORY CONTEXT (always included) - "Story foundation"
            try:
                current_story_id = get_current_story_id()
                core_story_context = get_core_story_context(current_story_id)
                
                if core_story_context:
                    context_messages.append({
                        "role": "system", 
                        "content": f"CORE STORY CONTEXT:\n{core_story_context}"
                    })
                    print(f"🔍 Debug: Added core story context to AI context ({len(core_story_context)} chars)")
                else:
                    print(f"🔍 Debug: No core story context available, skipping core context injection")
            except Exception as e:
                print(f"🔍 Debug: Error getting core story context: {e}")
            
            # 3. Scene state (always included) - "What's happening now"
            try:
                state_manager = StoryStateManager()
                current_state = state_manager.get_current_state()
                
                if current_state.get("characters"):
                    scene_state_prompt = state_manager.get_state_as_prompt()
                    context_messages.append({
                        "role": "system", 
                        "content": f"SCENE STATE TO MAINTAIN:\n{scene_state_prompt}"
                    })
                    print(f"🔍 Debug: Added scene state to AI context ({len(scene_state_prompt)} chars)")
                else:
                    print(f"🔍 Debug: No scene state available, skipping scene state injection")
            except Exception as e:
                print(f"🔍 Debug: State manager error in main chat: {e}")
            
            # 4. Key story points (memory) - "What led to this moment"
            try:
                key_memories = extract_key_story_points(session['history'])
                if key_memories:
                    memories_prompt = "\n".join([f"- {memory}" for memory in key_memories])
                    context_messages.append({
                        "role": "system", 
                        "content": f"KEY STORY POINTS:\n{memories_prompt}"
                    })
                    print(f"🔍 Debug: Added {len(key_memories)} key story points to AI context")
                else:
                    print(f"🔍 Debug: No key story points extracted")
            except Exception as e:
                print(f"🔍 Debug: Error extracting key story points: {e}")
            
            # 5. Recent conversation (last 2-3 messages) - "What just happened"
            if len(session['history']) > 0:
                print(f"🔍 Debug: Session history has {len(session['history'])} messages")
                for i, msg in enumerate(session['history']):
                    print(f"🔍 Debug: Message {i}: {msg['role']} - {msg['content'][:100]}...")
                
                # Use full history for better story continuity
                recent_history = session['history'][-3:]  # Use last 3 messages for all commands
                print(f"🔍 Debug: Using last {len(recent_history)} messages for continuity")
                context_messages.extend(recent_history)
            
            # 6. Current user input
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
        
        # Save conversation history for persistence
        current_story_id = get_current_story_id()
        save_conversation_history(session['history'], current_story_id, user_input, reply)
        
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
        
        # TTS will be generated on-demand via button, not automatically
        print(f"🔍 Debug: TTS enabled: {tts.enabled}")
        print(f"🔍 Debug: Reply length: {len(reply)}")
        print(f"🔍 Debug: TTS will be generated on-demand when user clicks 'Play TTS' button")
        
        # Clean up before sending response
        cleanup_resources()
        
        # Clean up request tracking before sending response
        if request_id:
            untrack_request(request_id)
            print(f"🔍 Debug: Request untracked: {request_id}")
        
        return jsonify({
            'message': reply,
            'type': 'assistant',
            'edge_triggered': False,  # Simplified for Render
            'audio_file': None  # Will be generated on-demand
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
    """Simple TTS status check - TTS is always enabled if API key is available"""
    try:
        return jsonify({
            'success': True,
            'enabled': tts.enabled,
            'mode_display': tts.get_mode_display(),
            'message': f"TTS: {tts.get_mode_display()}"
        })
    except Exception as e:
        print(f"🔍 Debug: Error checking TTS status: {e}")
        return jsonify({'error': f'Failed to check TTS status: {str(e)}'})

@app.route('/api/conversations', methods=['GET'])
def get_conversations():
    """Get list of available conversation history files"""
    try:
        ensure_conversations_dir()
        import glob
        
        # Get all conversation files
        conversation_files = glob.glob(os.path.join(CONVERSATIONS_DIR, "conversation_*.json"))
        conversation_files.sort(key=os.path.getctime, reverse=True)  # Sort by most recent
        
        conversations = []
        for filepath in conversation_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                filename = os.path.basename(filepath)
                conversations.append({
                    'filename': filename,
                    'story_id': data.get('story_id', 'general'),
                    'last_updated': data.get('last_updated', ''),
                    'message_count': data.get('message_count', 0),
                    'title': f"Story: {data.get('story_id', 'general').replace('_', ' ').title()}" if data.get('story_id') else "General Chat"
                })
            except Exception as e:
                print(f"🔍 Debug: Error reading conversation file {filepath}: {e}")
                continue
        
        return jsonify({'conversations': conversations})
        
    except Exception as e:
        print(f"🔍 Debug: Error listing conversations: {e}")
        return jsonify({'error': f'Failed to list conversations: {str(e)}'})

@app.route('/api/conversations/<filename>', methods=['GET'])
def get_conversation(filename):
    """Get a specific conversation file"""
    try:
        ensure_conversations_dir()
        filepath = os.path.join(CONVERSATIONS_DIR, filename)
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'Conversation file not found'})
        
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return jsonify(data)
        
    except Exception as e:
        print(f"🔍 Debug: Error reading conversation file: {e}")
        return jsonify({'error': f'Failed to read conversation: {str(e)}'})

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
        'mode_display': tts.get_mode_display(),
        'voice_id': tts.voice_id,
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

@app.route('/api/tts-generate', methods=['POST'])
def generate_tts_on_demand():
    """Generate TTS for the most recent AI response on demand"""
    try:
        if not tts.enabled:
            return jsonify({'error': 'TTS not enabled'})
        
        # Get the most recent AI response from session
        if 'history' not in session:
            return jsonify({'error': 'No conversation history found'})
        
        # Find the most recent assistant message
        assistant_messages = [msg for msg in session['history'] if msg['role'] == 'assistant']
        if not assistant_messages:
            return jsonify({'error': 'No AI response found to generate TTS for'})
        
        latest_response = assistant_messages[-1]['content']
        print(f"🔍 Debug: Generating TTS on-demand for response length: {len(latest_response)}")
        
        # Ensure voice ID is loaded fresh from file before generating TTS
        print(f"🔍 Debug: Ensuring voice ID is loaded from file before TTS generation")
        tts.voice_id = tts._load_voice_id()
        print(f"🔍 Debug: Using voice ID: {tts.voice_id}")
        
        # Generate TTS for the response
        if len(latest_response) < 2000:  # Short responses - generate immediately
            print(f"🔍 Debug: Short response - using immediate TTS")
            audio_file = tts.speak(latest_response, save_audio=True)
            if audio_file:
                print(f"🔍 Debug: TTS generated immediately: {audio_file}")
                return jsonify({
                    'success': True,
                    'audio_file': audio_file,
                    'message': 'TTS generated successfully'
                })
            else:
                return jsonify({'error': 'Failed to generate TTS'})
        else:  # Long responses - generate asynchronously
            print(f"🔍 Debug: Long response - using async TTS")
            # Create a simple request ID for TTS generation
            request_id = hashlib.md5(f"tts_on_demand:{len(latest_response)}:{time.time()}".encode()).hexdigest()[:8]
            audio_file = generate_tts_async(latest_response, save_audio=True, request_id=request_id)
            if audio_file == "generating":
                print(f"🔍 Debug: Async TTS started for on-demand request")
                return jsonify({
                    'success': True,
                    'audio_file': 'generating',
                    'message': 'TTS generation started'
                })
            else:
                return jsonify({'error': 'Failed to start TTS generation'})
                
    except Exception as e:
        print(f"🔍 Debug: Error generating TTS on-demand: {e}")
        import traceback
        print(f"🔍 Debug: TTS on-demand error traceback: {traceback.format_exc()}")
        return jsonify({'error': f'Failed to generate TTS: {str(e)}'})

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
                'api_key_set': bool(tts.api_key)
            },
            'file_system': {
                'current_dir': os.getcwd(),
                'files_in_dir': os.listdir('.')[:10],  # First 10 files
                'audio_dir_exists': os.path.exists('audio_files'),
                'voice_id_file_exists': os.path.exists('tts_voice_id.txt')
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

@app.route('/api/load-conversation', methods=['GET'])
def load_conversation():
    """Load the most recent conversation history"""
    try:
        # Get current story ID from session if available
        story_id = get_current_story_id()
        
        # Load conversation history
        history = load_conversation_history(story_id)
        
        if history:
            # Update session with loaded history
            session['history'] = history
            print(f"🔍 Debug: Loaded conversation history into session ({len(history)} messages)")
            
            return jsonify({
                'success': True,
                'history': history,
                'message': f'Loaded {len(history)} messages from conversation history'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'No conversation history found'
            })
            
    except Exception as e:
        print(f"🔍 Debug: Error loading conversation: {e}")
        import traceback
        return jsonify({
            'error': f'Failed to load conversation: {str(e)}',
            'traceback': traceback.format_exc()
        })

@app.route('/api/save-conversation', methods=['POST'])
def save_conversation():
    """Save the current conversation from the frontend"""
    try:
        data = request.get_json()
        if not data or 'history' not in data:
            return jsonify({'error': 'No conversation history provided'})
        
        history = data['history']
        if not history or len(history) == 0:
            return jsonify({'error': 'Empty conversation history'})
        
        # Get current story ID or create a general one
        story_id = get_current_story_id()
        if not story_id:
            story_id = f"conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Save conversation history
        success = save_conversation_history(history, story_id)
        
        if success:
            # Update session with the saved history
            session['history'] = history
            print(f"🔍 Debug: Saved and updated session with {len(history)} messages")
            
            return jsonify({
                'success': True,
                'message': f'Conversation saved successfully ({len(history)} messages)',
                'story_id': story_id
            })
        else:
            return jsonify({'error': 'Failed to save conversation to file'})
            
    except Exception as e:
        print(f"🔍 Debug: Error saving conversation: {e}")
        import traceback
        return jsonify({
            'error': f'Failed to save conversation: {str(e)}',
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
        logs.append(f"TTS Status: {tts.get_mode_display()}")
        logs.append(f"API Key Set: {'Yes' if os.getenv('XAI_API_KEY') else 'No'}")
        logs.append(f"Model: {os.getenv('XAI_MODEL', 'grok-3')}")
        logs.append("")
        
        # Check for common issues
        if not os.path.exists('audio_files'):
            logs.append("⚠️ Audio directory missing")
        if not os.path.exists('tts_voice_id.txt'):
            logs.append("⚠️ TTS voice ID file missing")
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

@app.route('/api/test-database', methods=['GET'])
def test_database():
    """Test endpoint to verify database connection and tables"""
    try:
        if not DATABASE_AVAILABLE:
            return jsonify({
                'success': False,
                'message': 'Database packages not available',
                'database_available': False
            })
        
        # Test database connection
        db_info = {
            'database_available': True,
            'database_url_set': bool(os.getenv('DATABASE_URL')),
            'database_uri': app.config.get('SQLALCHEMY_DATABASE_URI', 'Not set'),
            'connection_test': False,
            'tables_exist': False,
            'user_count': 0,
            'story_count': 0
        }
        
        try:
            # Test database connection
            db.session.execute('SELECT 1')
            db_info['connection_test'] = True
            print(f"🔍 Debug: Database connection successful")
            
            # Check if tables exist (works for both SQLite and PostgreSQL)
            if 'sqlite' in app.config.get('SQLALCHEMY_DATABASE_URI', ''):
                # SQLite
                result = db.session.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name IN ('users', 'stories')
                """)
            else:
                # PostgreSQL
                result = db.session.execute("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = 'public' AND table_name IN ('users', 'stories')
                """)
            
            tables = [row[0] for row in result.fetchall()]
            db_info['tables_exist'] = len(tables) == 2
            db_info['existing_tables'] = tables
            print(f"🔍 Debug: Tables found: {tables}")
            
            # Count records
            if 'users' in tables:
                user_count = db.session.execute('SELECT COUNT(*) FROM users').scalar()
                db_info['user_count'] = user_count
                print(f"🔍 Debug: User count: {user_count}")
            
            if 'stories' in tables:
                story_count = db.session.execute('SELECT COUNT(*) FROM stories').scalar()
                db_info['story_count'] = story_count
                print(f"🔍 Debug: Story count: {story_count}")
                
        except Exception as db_error:
            db_info['database_error'] = str(db_error)
            print(f"🔍 Debug: Database test failed: {db_error}")
        
        return jsonify({
            'success': True,
            'database_test': db_info,
            'message': 'Database test completed'
        })
        
    except Exception as e:
        print(f"🔍 Debug: Database test error: {e}")
        return jsonify({'error': f'Database test failed: {str(e)}'})

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

# Story Editor Routes
@app.route('/story-editor')
@require_auth
def story_editor():
    """Serve the story editor page"""
    return send_from_directory('templates', 'story_editor.html')

@app.route('/api/story-files', methods=['GET'])
@require_auth
def list_story_files():
    """List user's stories from database"""
    try:
        # Get current user from session
        user_id = session.get('db_user_id')
        google_id = session.get('user_id')  # Fallback to Google ID
        
        if not user_id and not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        # Use Google ID as fallback if database user ID not available
        if not user_id:
            print(f"🔍 Debug: Using Google ID as fallback: {google_id}")
            user_id = google_id
        
        if DATABASE_AVAILABLE:
            # Get user's stories from database
            user_stories = Story.query.filter_by(user_id=user_id).order_by(Story.updated_at.desc()).all()
            
            story_list = []
            for story in user_stories:
                content = story.content or {}
                story_list.append({
                    'story_id': story.story_id,
                    'title': story.title,
                    'characters': len(content.get('characters', {})),
                    'type': content.get('story_type', 'Unknown'),
                    'is_public': story.is_public,
                    'created_at': story.created_at.isoformat() if story.created_at else None,
                    'updated_at': story.updated_at.isoformat() if story.updated_at else None
                })
            
            print(f"🔍 Debug: Listed {len(story_list)} stories for user {user_id}")
            return jsonify({'story_files': story_list})
        else:
            # Fallback to file system if database not available
            import glob
            story_files = glob.glob("story_*.json")
            story_files.sort()
            
            file_list = []
            for filename in story_files:
                try:
                    with open(filename, 'r', encoding='utf-8') as f:
                        story_data = json.load(f)
                        file_list.append({
                            'filename': filename,
                            'title': story_data.get('title', filename),
                            'characters': len(story_data.get('characters', {})),
                            'type': story_data.get('story_type', 'Unknown')
                        })
                except Exception as e:
                    print(f"🔍 Debug: Error reading story file {filename}: {e}")
                    file_list.append({
                        'filename': filename,
                        'title': filename,
                        'characters': 0,
                        'type': 'Error'
                    })
            
            return jsonify({'story_files': file_list})
    except Exception as e:
        print(f"🔍 Debug: Error listing story files: {e}")
        return jsonify({'error': f'Could not list story files: {e}'}), 500

@app.route('/api/story-files/<story_id>', methods=['GET'])
@require_auth
def get_story_file(story_id):
    """Get a specific story from database"""
    try:
        # Get current user from session
        user_id = session.get('db_user_id')
        google_id = session.get('user_id')  # Fallback to Google ID
        
        if not user_id and not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        # Use Google ID as fallback if database user ID not available
        if not user_id:
            print(f"🔍 Debug: Using Google ID as fallback: {google_id}")
            user_id = google_id
        
        if DATABASE_AVAILABLE:
            # Get story from database
            story = Story.query.filter_by(story_id=story_id, user_id=user_id).first()
            
            if not story:
                return jsonify({'error': f'Story not found: {story_id}'}), 404
            
            print(f"🔍 Debug: Retrieved story {story_id} for user {user_id}")
            
            return jsonify({
                'success': True,
                'story': story.content,
                'metadata': {
                    'title': story.title,
                    'is_public': story.is_public,
                    'created_at': story.created_at.isoformat() if story.created_at else None,
                    'updated_at': story.updated_at.isoformat() if story.updated_at else None
                }
            })
        else:
            # Fallback to file system if database not available
            if not story_id.endswith('.json'):
                story_id += '.json'
            
            file_path = f"story_{story_id}" if not story_id.startswith('story_') else story_id
            
            if not os.path.exists(file_path):
                return jsonify({'error': f'Story file not found: {file_path}'}), 404
            
            with open(file_path, 'r', encoding='utf-8') as f:
                story_data = json.load(f)
            
            return jsonify({
                'success': True,
                'story': story_data
            })
    except Exception as e:
        print(f"🔍 Debug: Error reading story {story_id}: {e}")
        return jsonify({'error': f'Could not read story: {e}'}), 500

@app.route('/api/story-files', methods=['POST'])
@require_auth
def save_story_file():
    """Save a story file"""
    try:
        story_data = request.get_json()
        
        if not story_data or 'story_id' not in story_data:
            return jsonify({'error': 'Invalid story data'}), 400
        
        # Get current user from session
        user_id = session.get('db_user_id')
        google_id = session.get('user_id')  # Fallback to Google ID
        
        if not user_id and not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        # Use Google ID as fallback if database user ID not available
        if not user_id:
            print(f"🔍 Debug: Using Google ID as fallback: {google_id}")
            user_id = google_id
        
        # Extract story information
        story_id = story_data['story_id']
        title = story_data.get('title', f'Story {story_id}')
        is_public = story_data.get('is_public', False)
        
        # Add metadata
        story_data['metadata'] = {
            'created': datetime.now().isoformat(),
            'last_updated': datetime.now().isoformat(),
            'version': '1.0'
        }
        
        if DATABASE_AVAILABLE:
            # Check if story already exists
            existing_story = Story.query.filter_by(story_id=story_id, user_id=user_id).first()
            
            if existing_story:
                # Update existing story
                existing_story.title = title
                existing_story.content = story_data
                existing_story.is_public = is_public
                existing_story.updated_at = datetime.utcnow()
                db.session.commit()
                
                print(f"🔍 Debug: Story updated in database: {story_id} by user {user_id}")
                
                return jsonify({
                    'success': True,
                    'message': f'Story updated: {title}',
                    'story_id': story_id,
                    'action': 'updated'
                })
            else:
                # Create new story
                new_story = Story(
                    story_id=story_id,
                    title=title,
                    user_id=user_id,
                    content=story_data,
                    is_public=is_public
                )
                db.session.add(new_story)
                db.session.commit()
                
                print(f"🔍 Debug: Story saved to database: {story_id} by user {user_id}")
                
                return jsonify({
                    'success': True,
                    'message': f'Story saved: {title}',
                    'story_id': story_id,
                    'action': 'created'
                })
        else:
            # Fallback to file system if database not available
            filename = f"story_{story_id}.json"
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(story_data, f, indent=2, ensure_ascii=False)
            
            print(f"🔍 Debug: Story saved to file (database unavailable): {filename}")
            
            return jsonify({
                'success': True,
                'message': f'Story saved as {filename} (database unavailable)',
                'filename': filename,
                'action': 'created_file'
            })
    except Exception as e:
        print(f"🔍 Debug: Error saving story file: {e}")
        return jsonify({'error': f'Could not save story file: {e}'}), 500

def init_database():
    """Initialize database and run migrations"""
    if not DATABASE_AVAILABLE:
        print("⚠️ Database not available - skipping database initialization")
        return
        
    try:
        print("🗄️ Initializing database...")
        
        # Create all tables
        with app.app_context():
            db.create_all()
            print("✅ Database tables created successfully")
                
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        # Don't fail the app startup, just log the error
        import traceback
        print(f"Database error traceback: {traceback.format_exc()}")

if __name__ == '__main__':
    # Initialize database before starting the app
    init_database()
    
    # Set timeout for requests to prevent hung processes
    import signal
    
    def timeout_handler(signum, frame):
        print("⏰ Request timeout - cleaning up...")
        cleanup_resources()
        raise TimeoutError("Request timeout")
    
    # Set 5-minute timeout for requests
    signal.signal(signal.SIGALRM, timeout_handler)
    
    port = int(os.environ.get('PORT', 8080))
    print(f"🎭 Starting Grok Playground Web Interface on port {port}")
    print(f"📍 Local: http://localhost:{port}")
    print(f"🌐 Network: http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
