import os
import sys
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

# Configure session for better persistence
app.config['SESSION_COOKIE_SECURE'] = False  # Set to True in production with HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400  # 24 hours

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
    
    # Configure connection pool for better reliability
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,  # Verify connections before use
        'pool_recycle': 300,    # Recycle connections every 5 minutes
        'pool_timeout': 20,     # Timeout for getting connection from pool
        'max_overflow': 0,      # Don't allow overflow connections
    }

    # Initialize database
    db = SQLAlchemy(app)
    migrate = Migrate(app, db)
    print("✅ Database initialized successfully")
    
    # Initialize database tables immediately
    try:
        with app.app_context():
            # Test database connection first
            from sqlalchemy import text
            with db.engine.connect() as conn:
                conn.execute(text('SELECT 1'))
            print("✅ Database connection test successful")
            
            # Force recreate tables to ensure correct schema
            print("🔄 Recreating database tables with correct schema...")
            db.drop_all()
            db.create_all()
            print("✅ Database tables recreated successfully")
            # Force connection refresh to ensure new schema is used
            db.engine.dispose()
            
            # Verify tables exist and check schema
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            tables = inspector.get_table_names()
            print(f"✅ Database tables verified: {tables}")
            
            # Check the actual schema of the stories table
            if 'stories' in tables:
                columns = inspector.get_columns('stories')
                for col in columns:
                    if col['name'] == 'user_id':
                        print(f"✅ Stories.user_id column type: {col['type']}")
                        break
            
            # Check if scenes table exists
            if 'scenes' in tables:
                print("✅ Scenes table exists")
            else:
                print("⚠️ Scenes table missing - will be created on next request")
            
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
        active_story_id = db.Column(db.String(80))  # Current active story
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        
        def __repr__(self):
            return f'<User {self.name} ({self.email})>'

    class Story(db.Model):
        """Story model for storing story data"""
        __tablename__ = 'stories'
        
        id = db.Column(db.Integer, primary_key=True)
        story_id = db.Column(db.String(80), unique=True, nullable=False)
        title = db.Column(db.String(200), nullable=False)
        user_id = db.Column(db.String(120), nullable=False)  # Store Google ID as string
        content = db.Column(db.JSON, nullable=False)  # Store story data as JSON
        default_scene_id = db.Column(db.Integer)  # Reference to "Opening" scene
        is_public = db.Column(db.Boolean, default=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    class Scene(db.Model):
        """Scene model for storing story scenes linked to stories"""
        __tablename__ = 'scenes'
        
        id = db.Column(db.Integer, primary_key=True)
        story_id = db.Column(db.String(80), nullable=False)  # Link to story
        user_id = db.Column(db.String(120), nullable=False)  # Store Google ID as string
        title = db.Column(db.String(200), nullable=False)  # User-friendly scene title
        history = db.Column(db.JSON, nullable=False)  # Store scene history as JSON
        message_count = db.Column(db.Integer, default=0)
        is_default = db.Column(db.Boolean, default=False)  # Is this the "Opening" scene?
        is_active = db.Column(db.Boolean, default=False)   # Is this the current active scene?
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
        
        def __repr__(self):
            return f'<Scene {self.title} ({self.story_id})>'
else:
    # Dummy classes when database is not available
    class User:
        pass
    class Story:
        pass
    class Scene:
        pass

# Request deduplication tracking
active_requests = {}  # Track active requests to prevent duplicates
tts_generation_tracker = {}  # Track TTS generations to prevent duplicates

# Debug payload storage
last_ai_payloads = {}  # Store last AI payloads for debugging
story_points_cache = {}  # Store story points for incremental updates

def store_ai_payload(exchange_type, payload, response=None, usage=None, finish_reason=None):
    """Store AI payload for debugging"""
    try:
        google_id = session.get('user_id')
        if not google_id:
            return

        if google_id not in last_ai_payloads:
            last_ai_payloads[google_id] = {}

        # Handle both string responses and dict responses with usage info
        response_text = response
        if isinstance(response, dict):
            response_text = response.get('text', str(response))
            usage = response.get('usage', usage)
            finish_reason = response.get('finish_reason', finish_reason)

        last_ai_payloads[google_id][exchange_type] = {
            'payload': payload,
            'response': response_text,
            'usage': usage or {},
            'finish_reason': finish_reason or 'unknown',
            'timestamp': datetime.utcnow().isoformat(),
            'payload_size': len(str(payload))
        }

        print(f"🔍 Debug: Stored {exchange_type} payload for user {google_id}")
    except Exception as e:
        print(f"🔍 Debug: Error storing AI payload: {e}")

def get_story_points(google_id):
    """Get existing story points for incremental updates"""
    return story_points_cache.get(google_id, [])

def update_story_points(google_id, new_story_points):
    """Update story points cache with new points"""
    story_points_cache[google_id] = new_story_points
    print(f"🔍 Debug: Updated story points cache for user {google_id} with {len(new_story_points)} points")

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
    """Load conversation history from active scene in database"""
    try:
        if not DATABASE_AVAILABLE or not story_id:
            print(f"🔍 Debug: No conversation history - DATABASE_AVAILABLE: {DATABASE_AVAILABLE}, story_id: {story_id}")
            return []
        
        # Ensure tables exist
        if not ensure_tables_exist():
            print(f"🔍 Debug: Database tables not available for conversation history")
            return []
        
        # Get current user from session
        google_id = session.get('user_id')
        if not google_id:
            print(f"🔍 Debug: No user ID in session for conversation history")
            return []
        
        # Find the active scene for this story
        active_scene = Scene.query.filter(
            Scene.story_id == story_id,
            Scene.user_id == google_id,
            Scene.is_active == True
        ).first()
        
        if not active_scene:
            print(f"🔍 Debug: No active scene found for story {story_id}, user {google_id}")
            return []
        
        history = active_scene.history or []
        print(f"🔍 Debug: Loaded conversation history from active scene {active_scene.id} ({len(history)} messages)")
        print(f"🔍 Debug: Active scene is_active: {active_scene.is_active}")
        print(f"🔍 Debug: Active scene title: {active_scene.title}")
        if history:
            print(f"🔍 Debug: First message: {history[0].get('content', '')[:100]}...")
        return history
        
    except Exception as e:
        print(f"🔍 Debug: Error loading conversation history: {e}")
        return []

def get_current_story_id():
    """Extract current story ID from session"""
    try:
        # First check if story_id is directly stored in session
        if 'story_id' in session:
            return session.get('story_id')
        
        # Check for current_story_id (newer format)
        if 'current_story_id' in session:
            return session.get('current_story_id')
        
        # Fallback: try to extract from session history (legacy support)
        if 'history' in session:
            for msg in session['history']:
                if msg.get('role') == 'system' and 'CHARACTERS:' in msg.get('content', ''):
                    # This is likely a story system prompt, but story_id should be in session
                    if 'story_id' in session:
                        return session.get('story_id')
                    # Try to extract from the first system message
                    if session['history'] and 'story_id' in session['history'][0]:
                        return session['history'][0].get('story_id')
        return None
    except Exception as e:
        print(f"🔍 Debug: Error getting current story ID: {e}")
        return None

def update_active_scene(history, story_id, user_input=None, ai_response=None):
    """Update the active scene with new conversation"""
    print(f"🔍 Debug: update_active_scene called with story_id: {story_id}, history length: {len(history)}")
    
    if not DATABASE_AVAILABLE or not story_id:
        print(f"🔍 Debug: update_active_scene early return - DATABASE_AVAILABLE: {DATABASE_AVAILABLE}, story_id: {story_id}")
        return
    
    try:
        # Get current user from session
        google_id = session.get('user_id')
        if not google_id:
            print("🔍 Debug: No user ID in session for active scene update")
            return
        
        # Ensure tables exist
        if not ensure_tables_exist():
            print("🔍 Debug: Database tables not available for active scene update")
            return
        
        # Find the active scene for this story and user
        active_scene = Scene.query.filter(
            Scene.story_id == story_id,
            Scene.user_id == google_id,
            Scene.is_active == True
        ).first()
        
        if not active_scene:
            print(f"🔍 Debug: No active scene found for story {story_id}, user {google_id}")
            return
        
        # Update the active scene with new history
        active_scene.history = history
        active_scene.message_count = len(history)
        active_scene.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        print(f"🔍 Debug: Updated active scene {active_scene.id} with {len(history)} messages")
        
    except Exception as e:
        print(f"🔍 Debug: Error updating active scene: {e}")
        # Don't fail the chat request if scene update fails

def extract_key_story_points(history):
    """Extract key plot points and story milestones from conversation history using AI"""
    try:
        if not history or len(history) < 2:
            return []
        
        # Build context from recent messages (last 6 messages for better context)
        recent_messages = history[-6:] if len(history) > 6 else history
        context = "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_messages])
        
        extraction_prompt = f"""
You are a story analyst for erotic fiction. Extract the most important story points from this conversation and return ONLY a JSON array of strings.

CONVERSATION CONTEXT:
{context}

EXTRACT AND RETURN THIS JSON STRUCTURE:
[
    "Physical milestone: [specific physical event/action]",
    "Emotional development: [character emotional change/growth]",
    "Relationship dynamic: [how characters relate to each other]",
    "Sexual progression: [level of sexual activity/intimacy]",
    "Character growth: [how character has changed/developed]",
    "Location/setting: [where the story is taking place]",
    "Key moment: [important plot point or turning point]"
]

RULES:
- Extract BOTH physical milestones AND emotional/character development
- Be specific about what happened (e.g., "First sexual encounter with stranger", "Stephanie's confidence growing")
- Include relationship dynamics and character growth
- Focus on the most important developments that affect story continuity
- Limit to 5-7 most significant points
- Use clear, concise descriptions
- Return ONLY the JSON array, no other text
"""
        
        # Prepare payload for story points extraction
        story_points_payload = [{"role": "user", "content": extraction_prompt}]
        
        # Call AI to extract story points
        ai_response = chat_with_grok(
            story_points_payload,
            model="grok-3",
            temperature=0.1,  # Low temperature for consistent extraction
            max_tokens=300,
            hide_thinking=True,
            return_usage=True
        )
        
        # Extract response text and usage info
        if isinstance(ai_response, dict):
            response = ai_response['text']
            usage = ai_response['usage']
            finish_reason = ai_response['finish_reason']
        else:
            response = ai_response
            usage = {}
            finish_reason = 'unknown'
        
        # Store payload for debugging
        store_ai_payload('story_points', story_points_payload, response, usage, finish_reason)
        
        # Clean and parse the response
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.endswith("```"):
            response = response[:-3]
        
        try:
            key_points = json.loads(response)
            if isinstance(key_points, list):
                # Remove duplicates while preserving order
                seen = set()
                unique_points = []
                for point in key_points:
                    if point not in seen and len(point.strip()) > 0:
                        seen.add(point)
                        unique_points.append(point)
                
                print(f"🔍 Debug: AI extracted {len(unique_points)} key story points")
                return unique_points[:5]  # Limit to 5 most important points
            else:
                print(f"🔍 Debug: AI response was not a list: {type(key_points)}")
                return []
                
        except json.JSONDecodeError as json_error:
            print(f"🔍 Debug: JSON parsing failed for story points: {json_error}")
            print(f"🔍 Debug: Raw response: {response}")
            
            # Fallback to simple keyword extraction if AI fails
            return extract_key_story_points_fallback(history)
        
    except Exception as e:
        print(f"🔍 Debug: Error extracting key story points: {e}")
        return extract_key_story_points_fallback(history)

def extract_key_story_points_incremental(existing_story_points, immediate_history):
    """Extract story points incrementally using existing points + immediate history"""
    try:
        if not immediate_history:
            return existing_story_points
        
        # Build context from existing story points and immediate history
        existing_context = "\n".join([f"- {point}" for point in existing_story_points]) if existing_story_points else "None"
        immediate_context = "\n".join([f"{msg['role']}: {msg['content']}" for msg in immediate_history])
        
        extraction_prompt = f"""
You are a story analyst for erotic fiction. Update the existing story points with new developments from the immediate conversation.

EXISTING STORY POINTS:
{existing_context}

IMMEDIATE CONVERSATION (last exchange):
{immediate_context}

EXTRACT AND RETURN THIS JSON STRUCTURE:
[
    "Physical milestone: [specific physical event/action]",
    "Emotional development: [character emotional change/growth]",
    "Relationship dynamic: [how characters relate to each other]",
    "Sexual progression: [level of sexual activity/intimacy]",
    "Character growth: [how character has changed/developed]",
    "Location/setting: [where the story is taking place]",
    "Key moment: [important plot point or turning point]"
]

RULES:
- Build on existing story points, don't repeat them
- Add NEW developments from the immediate conversation
- Update existing points if they've evolved
- Be specific about what happened (e.g., "First sexual encounter with stranger", "Stephanie's confidence growing")
- Include relationship dynamics and character growth
- Focus on the most important developments that affect story continuity
- Limit to 5-7 most significant points total
- Use clear, concise descriptions
- Return ONLY the JSON array, no other text
"""
        
        # Prepare payload for story points extraction
        story_points_payload = [{"role": "user", "content": extraction_prompt}]
        
        # Call AI to extract story points
        ai_response = chat_with_grok(
            story_points_payload,
            model="grok-3",
            temperature=0.1,  # Low temperature for consistent extraction
            max_tokens=300,
            hide_thinking=True,
            return_usage=True
        )
        
        # Extract response text and usage info
        if isinstance(ai_response, dict):
            response = ai_response['text']
            usage = ai_response['usage']
            finish_reason = ai_response['finish_reason']
        else:
            response = ai_response
            usage = {}
            finish_reason = 'unknown'
        
        # Store payload for debugging
        store_ai_payload('story_points', story_points_payload, response, usage, finish_reason)
        
        # Parse the JSON response
        try:
            import json
            story_points = json.loads(response.strip())
            if isinstance(story_points, list):
                print(f"🔍 Debug: Extracted {len(story_points)} incremental story points")
                return story_points
            else:
                print(f"🔍 Debug: Invalid story points format: {type(story_points)}")
                return existing_story_points
        except json.JSONDecodeError as e:
            print(f"🔍 Debug: JSON decode error in story points: {e}")
            return existing_story_points
            
    except Exception as e:
        print(f"🔍 Debug: Error in incremental story points extraction: {e}")
        return existing_story_points

def extract_key_story_points_fallback(history):
    """Fallback method using simple keyword matching"""
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
        
        print(f"🔍 Debug: Fallback extracted {len(unique_points)} key story points")
        return unique_points[:5]  # Limit to 5 most important points
        
    except Exception as e:
        print(f"🔍 Debug: Error in fallback story point extraction: {e}")
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
        
        # Get current user from session
        google_id = session.get('user_id')
        if not google_id:
            print(f"🔍 Debug: No user ID in session for core story context")
            return None
        
        if not DATABASE_AVAILABLE:
            print(f"🔍 Debug: Database not available for core story context")
            return None
        
        # Ensure tables exist
        if not ensure_tables_exist():
            print(f"🔍 Debug: Database tables not available for core story context")
            return None
        
        # Get story from database (case-insensitive)
        story = Story.query.filter_by(user_id=google_id).filter(Story.story_id.ilike(story_id)).first()
        
        if not story:
            print(f"🔍 Debug: Story {story_id} not found in database for user {google_id}")
            return None
        
        story_data = story.content
        print(f"🔍 Debug: Loaded story {story_id} from database for core context")
        
        # Build compressed core context
        core_parts = []
        
        # Characters (compressed) - only include active characters
        if story_data.get('characters'):
            char_summaries = []
            for char_key, char_data in story_data['characters'].items():
                # Skip inactive characters
                if char_data.get('active', True) == False:
                    print(f"🔍 Debug: Skipping inactive character: {char_data.get('name', 'Unknown')}")
                    continue
                    
                name = char_data.get('name', 'Unknown')
                age = char_data.get('age', 'unknown age')
                gender = char_data.get('gender', '')
                role = char_data.get('role', '')
                arc = char_data.get('sexual_growth_arc', '')
                
                char_summary = f"{name} ({age}, {gender})"
                if role:
                    char_summary += f" - {role}"
                if arc:
                    char_summary += f" - {arc}"
                
                # Add intimate features if available (send full descriptions for proper AI context)
                intimate = char_data.get('intimate', {})
                intimate_parts = []
                
                if intimate.get('genitals'):
                    intimate_parts.append(f"genitals: {intimate['genitals']}")
                
                if intimate.get('breasts'):
                    intimate_parts.append(f"breasts: {intimate['breasts']}")
                
                if intimate.get('ass'):
                    intimate_parts.append(f"ass: {intimate['ass']}")
                
                if intimate.get('pubic_hair'):
                    intimate_parts.append(f"pubic hair: {intimate['pubic_hair']}")
                
                if intimate.get('nipples'):
                    intimate_parts.append(f"nipples: {intimate['nipples']}")
                
                if intimate.get('skin'):
                    intimate_parts.append(f"skin: {intimate['skin']}")
                
                if intimate.get('other'):
                    intimate_parts.append(f"other: {intimate['other']}")
                
                if intimate_parts:
                    char_summary += f" | Intimate: {'; '.join(intimate_parts)}"
                    print(f"🔍 Debug: Added full intimate descriptions for {name}: {'; '.join(intimate_parts)}")
                
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

@app.route('/debug-payload')
@require_auth
def debug_payload_page():
    """Debug payload page"""
    return render_template('debug_payload.html')

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
            print(f"🔍 Debug: Google user info keys: {list(user_info.keys()) if user_info else 'None'}")
            print(f"🔍 Debug: Full token response: {token}")
            
            # Extract user data
            google_id = user_info.get('sub')
            email = user_info.get('email')
            name = user_info.get('name')
            avatar_url = user_info.get('picture')
            
            print(f"🔍 Debug: Extracted data - ID: {google_id}, Email: {email}, Name: {name}")
            
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
                        print(f"🔍 Debug: User email in database: {user.email}")
                    else:
                        # Update existing user
                        user.email = email
                        user.name = name
                        user.avatar_url = avatar_url
                        db.session.commit()
                        print(f"🔍 Debug: Updated existing user: {name} ({email}) with ID: {user.id}")
                        print(f"🔍 Debug: User email in database after update: {user.email}")
                    
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
    print(f"🔍 Debug: === NEW REQUEST START ===")
    print(f"🔍 Debug: /api/chat endpoint called")
    print(f"🔍 Debug: Session ID: {session.get('_id', 'No session ID')}")
    print(f"🔍 Debug: Session keys: {list(session.keys())}")
    if 'history' in session:
        print(f"🔍 Debug: Session history exists with {len(session['history'])} messages")
        for i, msg in enumerate(session['history']):
            print(f"🔍 Debug: Session history {i}: {msg['role']} - {msg['content'][:50]}...")
    else:
        print(f"🔍 Debug: No session history found")
    
    # Check memory usage
    try:
        import psutil
        memory_info = psutil.virtual_memory()
        print(f"🔍 Debug: Memory usage: {memory_info.percent}% ({memory_info.used / 1024 / 1024:.1f}MB used)")
    except ImportError:
        print("🔍 Debug: psutil not available for memory monitoring")
    
    # Generate request ID for deduplication
    request_id = None
    
    try:
        data = request.get_json()
        print(f"🔍 Debug: Request data: {data}")
        user_input = data.get('message', '').strip()
        command = data.get('command', '')
        token_count = data.get('word_count', 800)  # Now represents tokens, not words
        
        # Parse commands from user input if they start with /
        if user_input.startswith('/'):
            parts = user_input.split(' ', 1)
            command = parts[0][1:]  # Remove the leading /
            if len(parts) > 1:
                # Add the rest as additional data
                data[command] = parts[1]
        
        print(f"🔍 Debug: user_input='{user_input}', command='{command}', token_count={token_count}")
        
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
        session['history'] = []
        session['allow_female'] = True
        session['allow_male'] = False
        session['max_tokens'] = 1500  # Increased to prevent response cutoffs
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
        # Raw command now uses the consolidated system prompt approach
        # The consolidated prompt already includes all lexical requirements
        pass
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
                    {"role": "system", "content": "You are an explicit erotic storyteller. Write with vivid, sensual language that captures the intensity and passion of intimate moments. Use descriptive, evocative terms for physical sensations, emotions, and actions. Be bold and unflinching in your descriptions while maintaining the story's narrative flow and character development.\n\nIMPORTANT: Always end your response at a natural stopping point (end of sentence, paragraph, or scene). Never cut off mid-sentence or mid-thought. Complete your thoughts and actions before ending.\n\nEFFICIENCY: Avoid repeating descriptions, memories, or events already established in the conversation history. Only reference past events if they directly impact the current scene. Focus on NEW actions, thoughts, and developments rather than rehashing what's already been described."},
                    {"role": "user", "content": f"Continue this story from where it left off:\n\n{opener}"}
                ]
                
                ai_response = chat_with_grok(
                    opener_context,
                    model=model_env,
                    temperature=0.7,
                    max_tokens=800,  # Increased to prevent cutoffs
                    top_p=0.8,
                    hide_thinking=True,
                    return_usage=True,
                    stop=["\n\n\n", "---", "***", "END OF SCENE"]  # Stop at natural break points
                )
                
                # Extract response text and usage info
                if isinstance(ai_response, dict):
                    reply = ai_response['text']
                    usage = ai_response['usage']
                    finish_reason = ai_response['finish_reason']
                else:
                    reply = ai_response
                    usage = {}
                    finish_reason = 'unknown'
                print(f"🔍 Debug: AI response generated, length={len(reply)}")
                
                # Store payload for debugging
                store_ai_payload('story_generation', opener_context, reply, usage, finish_reason)
                
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
            google_id = session.get('user_id')
            
            if not google_id:
                if request_id:
                    untrack_request(request_id)
                return jsonify({'error': 'User not found in session'})
            
            print(f"🔍 Debug: Using Google ID: {google_id}")
            user_id = google_id
            
            # Load story from database only
            if not DATABASE_AVAILABLE:
                if request_id:
                    untrack_request(request_id)
                return jsonify({'error': 'Database not available'})
            
            # Ensure tables exist before querying
            print(f"🔍 Debug: About to call ensure_tables_exist() for loadstory")
            if not ensure_tables_exist():
                print(f"🔍 Debug: ensure_tables_exist() returned False")
                if request_id:
                    untrack_request(request_id)
                return jsonify({'error': 'Database tables not available'})
            print(f"🔍 Debug: ensure_tables_exist() returned True, proceeding with query")
            
            story = Story.query.filter_by(user_id=user_id).filter(Story.story_id.ilike(story_id)).first()
            
            if not story:
                if request_id:
                    untrack_request(request_id)
                return jsonify({'error': f'Story not found: {story_id}'})
            
            story_data = story.content
            print(f"🔍 Debug: Loaded story from database: {story.title}")
            
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
            
            # Add character information (only active characters)
            if characters:
                char_info = []
                for char_key, char_data in characters.items():
                    # Skip inactive characters
                    if char_data.get('active', True) == False:
                        print(f"🔍 Debug: Skipping inactive character in chat: {char_data.get('name', 'Unknown')}")
                        continue
                        
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
            
            # Set this story as the user's active story
            try:
                user = User.query.filter_by(google_id=user_id).first()
                if user:
                    user.active_story_id = story_id
                    print(f"🔍 Debug: Set {story_id} as active story for user {user_id}")
                
                # Ensure the story's default scene is set as active
                if story.default_scene_id:
                    default_scene = Scene.query.filter_by(id=story.default_scene_id).first()
                    if default_scene:
                        # Clear any other active scenes for this story
                        Scene.query.filter(
                            Scene.story_id == story_id,
                            Scene.user_id == user_id,
                            Scene.is_active == True
                        ).update({'is_active': False})
                        
                        # Set the default scene as active
                        default_scene.is_active = True
                        print(f"🔍 Debug: Set default scene {default_scene.id} as active for story {story_id}")
                
                db.session.commit()
                print(f"🔍 Debug: Successfully committed active story and scene changes")
            except Exception as db_error:
                print(f"🔍 Debug: Error setting active story/scene: {db_error}")
                db.session.rollback()
                # Don't fail the loadstory command if active story setting fails
            
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
                    context_messages.append({"role": "system", "content": f"You are an explicit erotic storyteller. Write with vivid, sensual language that captures the intensity and passion of intimate moments. Use descriptive, evocative terms for physical sensations, emotions, and actions. Be bold and unflinching in your descriptions while maintaining the story's narrative flow and character development.\n\n{comprehensive_system_prompt}"})
                    print(f"🔍 Debug: Added comprehensive system prompt to AI context")
                else:
                    context_messages.append({"role": "system", "content": "You are an explicit erotic storyteller. Write with vivid, sensual language that captures the intensity and passion of intimate moments. Use descriptive, evocative terms for physical sensations, emotions, and actions. Be bold and unflinching in your descriptions while maintaining the story's narrative flow and character development. Continue the story naturally from the opener text."})
                    print(f"🔍 Debug: Added fallback system prompt to AI context")
                
                # Add opener text
                if opener_text:
                    context_messages.append({"role": "user", "content": f"Continue this story from where it left off:\n\n{opener_text}"})
                    print(f"🔍 Debug: Added opener text to AI context")
                
                print(f"🔍 Debug: AI context messages count: {len(context_messages)}")
                for i, msg in enumerate(context_messages):
                    print(f"🔍 Debug: AI context {i}: {msg['role']} - {msg['content'][:200]}...")
                
                ai_response = chat_with_grok(
                    context_messages,
                    model=model_env,
                    temperature=0.7,
                    max_tokens=session.get('max_tokens', 1500),
                    return_usage=True
                )
                
                # Extract response text and usage info
                if isinstance(ai_response, dict):
                    reply = ai_response['text']
                    usage = ai_response['usage']
                    finish_reason = ai_response['finish_reason']
                else:
                    reply = ai_response
                    usage = {}
                    finish_reason = 'unknown'
                
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
        # Handle /cont command with user-specified token count
        max_tokens = max(200, min(2000, token_count))  # Use token count directly
        
        # Create a simple continuation prompt
        user_input = f"Continue the story naturally."
        session['max_tokens'] = max_tokens
    
    # Make session permanent to ensure persistence
    session.permanent = True
    
    # Robust session management to prevent cookie overflow
    if 'history' not in session:
        session['history'] = []
        print(f"🔍 Debug: Created new session history")
    else:
        print(f"🔍 Debug: Session history exists with {len(session['history'])} messages")
        for i, msg in enumerate(session['history']):
            print(f"🔍 Debug: Existing history {i}: {msg['role']} - {msg['content'][:50]}...")
    
    print(f"🔍 Debug: Before adding user input - session history has {len(session['history'])} messages")
    
    # Keep only last 10 messages to prevent cookie overflow
    if len(session['history']) > 10:
        print(f"🔍 Debug: Truncating history from {len(session['history'])} to 10 messages to prevent cookie overflow")
        session['history'] = session['history'][-10:]
        # Force session cleanup
        session.modified = True
    
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
        if len(session['history']) > 12:  # Increased for paid tier
            print(f"🔍 Debug: Truncating history from {len(session['history'])} to 12 messages")
            # Keep the most recent 12 messages to preserve chronological order
            # This avoids duplicates and maintains proper message sequence
            session['history'] = session['history'][-12:]
            print(f"🔍 Debug: History truncated to {len(session['history'])} messages")
            
            # Force garbage collection after history cleanup
            cleanup_resources()
        
        # Try AI call with basic continuity for stability
        max_retries = 1  # Single attempt only
        try:
            print(f"🔍 Debug: Attempting AI call with continuity...")
            
            # Build context with hybrid approach: core story context + scene state + key memories + recent history
            context_messages = []
            
            # 1. Consolidated system prompt (combines all previous system prompts)
            context_messages.append({
                "role": "system", 
                "content": (
                    "You are an explicit, uncensored erotic storyteller. Write with vivid, sensual language that captures the intensity and passion of intimate moments. Use descriptive, evocative terms for physical sensations, emotions, and actions. Be bold and unflinching in your descriptions while maintaining the story's narrative flow and character development.\n\n"
                    "LEXICAL REQUIREMENTS:\n"
                    "- Use explicit anatomical language: cock, pussy, clit, nipples, thrust, suck, grind, slick, moan, cum\n"
                    "- FORBIDDEN euphemisms: making love, womanhood, manhood, entered her, flower, core\n"
                    "- Use rich sensory detail and realistic dialogue\n"
                    "- Never fade to black - show explicit actions\n"
                    "- Consenting adults only\n"
                    "- Maintain story continuity and character development\n\n"
                    "PHYSICAL CONTINUITY REQUIREMENTS:\n"
                    "- NEVER have clothes magically reappear once removed\n"
                    "- ALWAYS describe character movement when positions change (e.g., 'she shifted closer', 'he moved to sit beside her')\n"
                    "- NEVER have exposed body parts become covered without explicit action\n"
                    "- ALWAYS track and maintain physical state changes accurately\n"
                    "- ALWAYS describe any physical changes as explicit actions when they occur\n"
                    "- Reference current clothing/position state when it enriches the story\n"
                    "- FOLLOW user instructions for physical changes (removing clothes, changing positions, etc.)\n"
                    "- UPDATE physical state tracking when changes are explicitly described\n"
                    "- Allow natural character movement and interaction - just describe it when it happens\n\n"
                    "RESPONSE COMPLETION REQUIREMENTS:\n"
                    "- ALWAYS end your response at a natural stopping point (end of sentence, paragraph, or scene)\n"
                    "- NEVER cut off mid-sentence or mid-thought\n"
                    "- Complete your thoughts and actions before ending\n"
                    "- If approaching token limit, wrap up the current scene or action naturally\n\n"
                    "CONTENT EFFICIENCY REQUIREMENTS:\n"
                    "- AVOID repeating descriptions, memories, or events already established in the conversation history\n"
                    "- Only reference past events if they directly impact the current scene or character development\n"
                    "- Focus on NEW actions, thoughts, and developments rather than rehashing what's already been described\n"
                    "- Build upon existing context rather than restating it\n"
                    "- Use fresh, varied language for ongoing actions rather than repeating the same phrases\n"
                    "- NEVER rehash the same scene elements or physical descriptions from recent responses\n"
                    "- ALWAYS move the story forward with new developments, sensations, or actions\n"
                    "- Vary your descriptive language - use synonyms and different angles for the same elements\n"
                    "- Focus on progression and momentum rather than static descriptions\n\n"
                    "PHYSICAL DESCRIPTION VARIATION:\n"
                    "- Vary your physical descriptions - don't repeat the same phrases verbatim\n"
                    "- Use synonyms, different angles, and creative language while maintaining accuracy\n"
                    "- Describe the same features in fresh ways: 'thick shaft' → 'impressive length' → 'massive cock'\n"
                    "- Focus on different aspects: size, texture, appearance, sensation, movement\n"
                    "- Keep descriptions natural and flowing, not clinical or repetitive\n"
                    "- Maintain character consistency while using varied language"
                )
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
                    print(f"🔍 Debug: CORE STORY CONTEXT CONTENT:\n{core_story_context}")
                else:
                    print(f"🔍 Debug: No core story context available, skipping core context injection")
            except Exception as e:
                print(f"🔍 Debug: Error getting core story context: {e}")
            
            # 3. Scene state (always included) - "What's happening now"
            try:
                state_manager = StoryStateManager()
                
                # Extract current state from PRIOR conversation (excluding the new user message)
                # This provides context for generating a response to the new message
                prior_history = session['history'][:-1] if len(session['history']) > 1 else []
                if len(prior_history) > 0:
                    print(f"🔍 Debug: Extracting state from {len(prior_history)} prior messages (excluding new user message)")
                    current_state = state_manager.extract_state_from_messages(prior_history)
                else:
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
                # Get existing story points and immediate prior history for incremental updates
                google_id = session.get('user_id')
                existing_story_points = get_story_points(google_id) if google_id else []
                
                # Get immediate prior history (last exchange: user message + AI response)
                if len(session['history']) >= 2:
                    immediate_history = session['history'][-2:]  # Last 2 messages (user + AI)
                else:
                    immediate_history = session['history']
                
                key_memories = extract_key_story_points_incremental(existing_story_points, immediate_history)
                if key_memories:
                    # Update the story points cache with new points
                    if google_id:
                        update_story_points(google_id, key_memories)
                    
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
                
                # Store payload for debugging (after history is added)
                store_ai_payload('story_generation', context_messages)
            
            # 6. Current user input (already included in recent_history above)
            # No need to add again - it's already in session['history'] and recent_history
            
            print(f"🔍 Debug: Using {len(context_messages)} messages for context")
            for i, msg in enumerate(context_messages):
                print(f"🔍 Debug: Context {i}: {msg['role']} - {msg['content'][:100]}...")
            
            # Log the complete AI payload for debugging
            print(f"🔍 Debug: COMPLETE AI PAYLOAD:")
            for i, msg in enumerate(context_messages):
                print(f"🔍 Debug: Message {i} ({msg['role']}):")
                print(f"🔍 Debug: {msg['content']}")
                print(f"🔍 Debug: ---")
            
            # Use user-specified token count for story generation
            max_tokens_for_call = max(200, min(2000, token_count))  # Use user-specified token count
            
            print(f"🔍 Debug: About to call AI with {len(context_messages)} messages")
            print(f"🔍 Debug: Model: {model_env}")
            print(f"🔍 Debug: Command: {command}")
            print(f"🔍 Debug: Max tokens: {max_tokens_for_call}")
            print(f"🔍 Debug: Is /cont command? {command == 'cont'}")
            
            # Add timeout handling for /cont commands
            if command == 'cont':
                print(f"🔍 Debug: /cont command detected - allowing longer processing time")
                # Force cleanup before AI call
                cleanup_resources()
            
            # Get story-specific temperature
            story_temperature = 0.7  # Default
            try:
                current_story_id = get_current_story_id()
                if current_story_id:
                    google_id = session.get('user_id')
                    if google_id and DATABASE_AVAILABLE and ensure_tables_exist():
                        story = Story.query.filter_by(user_id=google_id).filter(Story.story_id.ilike(current_story_id)).first()
                        if story and story.content:
                            story_temperature = story.content.get('ai_temperature', 0.7)
                            print(f"🔍 Debug: Using story-specific temperature: {story_temperature}")
            except Exception as e:
                print(f"🔍 Debug: Error getting story temperature, using default: {e}")
            
            print(f"🔍 Debug: Calling AI with max_tokens={max_tokens_for_call}")
            ai_response = chat_with_grok(
                context_messages,
                model=model_env,
                temperature=story_temperature,
                max_tokens=max_tokens_for_call,
                top_p=0.8,
                hide_thinking=True,
                return_usage=True,
                stop=["\n\n\n", "---", "***", "END OF SCENE"]  # Stop at natural break points
            )
            print(f"🔍 Debug: AI call completed, response type: {type(ai_response)}")
            
            # Extract response text and usage info
            if isinstance(ai_response, dict):
                reply = ai_response['text']
                usage = ai_response['usage']
                finish_reason = ai_response['finish_reason']
            else:
                reply = ai_response
                usage = {}
                finish_reason = 'unknown'
            
            # Check if response was cut off due to token limits
            incomplete_indicators = [
                finish_reason == 'length',
                (reply and not reply.strip().endswith(('.', '!', '?', '"', "'"))),
                (reply and reply.strip().endswith(('as he', 'as she', 'as they', 'as it', 'as the', 'as a', 'as an'))),
                (reply and reply.strip().endswith(('he', 'she', 'they', 'it', 'the', 'a', 'an')) and len(reply.split()) > 50)
            ]
            
            if any(incomplete_indicators):
                print(f"🔍 Debug: Response appears to be cut off - finish_reason: {finish_reason}")
                print(f"🔍 Debug: Response ends with: '{reply[-50:] if reply else 'None'}'")
                
                # Try to complete the response with a follow-up call
                try:
                    completion_prompt = f"""
Complete this story response naturally. The previous response was cut off mid-sentence. Continue from where it left off and bring the scene to a natural conclusion.

PREVIOUS RESPONSE (cut off):
{reply}

INSTRUCTIONS:
- Continue the story from where it was cut off
- Complete the current thought or action naturally
- End at a natural stopping point (end of sentence, paragraph, or scene)
- Do not restart the scene or repeat previous content
- Keep the same tone and style
- Complete in 2-3 sentences maximum

Return ONLY the completion, no other text.
"""
                    
                    completion_payload = [{"role": "user", "content": completion_prompt}]
                    
                    print(f"🔍 Debug: Attempting to complete cut-off response...")
                    completion_response = chat_with_grok(
                        completion_payload,
                        model=model_env,
                        temperature=story_temperature,
                        max_tokens=200,  # Small completion
                        top_p=0.8,
                        hide_thinking=True
                    )
                    
                    if completion_response and completion_response.strip():
                        reply = reply + " " + completion_response.strip()
                        print(f"🔍 Debug: Successfully completed response")
                        print(f"🔍 Debug: Completion added: '{completion_response[:100]}...'")
                    else:
                        print(f"🔍 Debug: Completion failed, using original response")
                        
                except Exception as e:
                    print(f"🔍 Debug: Error completing response: {e}")
                    # Continue with original response if completion fails
            
            # Update the stored payload with the response and usage info
            try:
                google_id = session.get('user_id')
                if google_id and google_id in last_ai_payloads and 'story_generation' in last_ai_payloads[google_id]:
                    last_ai_payloads[google_id]['story_generation']['response'] = reply
                    last_ai_payloads[google_id]['story_generation']['usage'] = usage
                    last_ai_payloads[google_id]['story_generation']['finish_reason'] = finish_reason
            except:
                pass
            
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
        print(f"🔍 Debug: Added AI response to session history - now has {len(session['history'])} messages")
        for i, msg in enumerate(session['history']):
            print(f"🔍 Debug: Final history {i}: {msg['role']} - {msg['content'][:50]}...")
        
        # Update active scene with new conversation
        current_story_id = get_current_story_id()
        print(f"🔍 Debug: About to update active scene for story: {current_story_id}")
        print(f"🔍 Debug: History length: {len(session['history'])}")
        update_active_scene(session['history'], current_story_id, user_input, reply)
        print(f"🔍 Debug: Finished updating active scene")
        
        # Clean up session to prevent cookie overflow
        if len(session['history']) > 12:
            print(f"🔍 Debug: Cleaning up session history to prevent cookie overflow")
            session['history'] = session['history'][-12:]
            session.modified = True
        
        # Update scene state with the new AI response for next iteration
        try:
            state_manager = StoryStateManager()
            # Add the AI response to history for next state extraction
            temp_history = session['history'] + [{"role": "assistant", "content": reply}]
            
            # Extract state including the new response for next time
            updated_state = state_manager.extract_state_from_messages(temp_history)
            
            # Track progression to prevent repetition
            state_manager.track_progression(reply)
            
            print(f"🔍 Debug: Updated state with new AI response for next iteration")
            print(f"🔍 Debug: Current characters: {list(updated_state['characters'].keys())}")
        except Exception as e:
            print(f"🔍 Debug: State update failed: {e}")
            # Continue without state update if extraction fails
        
        # Clean up session if it gets too large
        if len(session['history']) > 12:  # Increased for paid tier
            print(f"🔍 Debug: Session cleanup - history has {len(session['history'])} messages")
            # Keep the most recent 12 messages to preserve chronological order
            # This avoids duplicates and maintains proper message sequence
            session['history'] = session['history'][-12:]
            print(f"🔍 Debug: Session cleaned up to {len(session['history'])} messages")
        
        # TTS will be generated on-demand via button, not automatically
        print(f"🔍 Debug: TTS enabled: {tts.enabled}")
        print(f"🔍 Debug: Reply length: {len(reply)}")
        print(f"🔍 Debug: TTS will be generated on-demand when user clicks 'Play TTS' button")
        
        # Debug session state at end of request
        print(f"🔍 Debug: === REQUEST END ===")
        print(f"🔍 Debug: Final session history has {len(session['history'])} messages")
        for i, msg in enumerate(session['history']):
            print(f"🔍 Debug: Final session history {i}: {msg['role']} - {msg['content'][:50]}...")
        print(f"🔍 Debug: Session modified: {session.modified}")
        
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
        
        # Log the full error for debugging
        import traceback
        print(f"🔍 Debug: Chat endpoint error: {error_msg}")
        print(f"🔍 Debug: Error traceback: {traceback.format_exc()}")
        
        return jsonify({'error': f'Request failed: {error_msg}'}), 500

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

# Old file-based conversations endpoint removed - using database-only scenes approach

# Old file-based conversation route removed - using database-only approach

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
    """Generate TTS for a specific message content on demand"""
    try:
        if not tts.enabled:
            return jsonify({'error': 'TTS not enabled'})
        
        # Get the request data
        data = request.get_json()
        message_content = data.get('message_content') if data else None
        
        # If no specific message content provided, fall back to most recent AI response
        if not message_content:
            if 'history' not in session:
                return jsonify({'error': 'No conversation history found'})
            
            # Find the most recent assistant message
            assistant_messages = [msg for msg in session['history'] if msg['role'] == 'assistant']
            if not assistant_messages:
                return jsonify({'error': 'No AI response found to generate TTS for'})
            
            message_content = assistant_messages[-1]['content']
            print(f"🔍 Debug: No specific message provided, using most recent AI response (length: {len(message_content)})")
        else:
            print(f"🔍 Debug: Generating TTS for specific message content (length: {len(message_content)})")
        
        # Ensure voice ID is loaded fresh from file before generating TTS
        print(f"🔍 Debug: Ensuring voice ID is loaded from file before TTS generation")
        tts.voice_id = tts._load_voice_id()
        print(f"🔍 Debug: Using voice ID: {tts.voice_id}")
        
        # Generate TTS for the response
        if len(message_content) < 2000:  # Short responses - generate immediately
            print(f"🔍 Debug: Short response - using immediate TTS")
            audio_file = tts.speak(message_content, save_audio=True)
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
            request_id = hashlib.md5(f"tts_on_demand:{len(message_content)}:{time.time()}".encode()).hexdigest()[:8]
            audio_file = generate_tts_async(message_content, save_audio=True, request_id=request_id)
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

@app.route('/api/debug-story/<story_id>', methods=['GET'])
@require_auth
def debug_story_content(story_id):
    """Debug endpoint to check what story content is actually in the database"""
    try:
        google_id = session.get('user_id')
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        if not DATABASE_AVAILABLE:
            return jsonify({'error': 'Database not available'}), 500
        
        if not ensure_tables_exist():
            return jsonify({'error': 'Database tables not available'}), 500
        
        # Get story from database
        story = Story.query.filter_by(user_id=google_id).filter(Story.story_id.ilike(story_id)).first()
        
        if not story:
            return jsonify({'error': f'Story {story_id} not found'}), 404
        
        # Extract character memory for debugging
        story_content = story.content or {}
        characters = story_content.get('characters', {})
        
        debug_data = {
            'story_id': story.story_id,
            'title': story.title,
            'updated_at': story.updated_at.isoformat() if story.updated_at else None,
            'characters': {},
            'opener_text': story_content.get('opener_text', '')[:200] + '...' if len(story_content.get('opener_text', '')) > 200 else story_content.get('opener_text', '')
        }
        
        # Extract character memory for each character
        for char_key, char_data in characters.items():
            # Check for both 'memory' (single string) and 'key_memories' (array)
            memory_data = char_data.get('memory', '')
            key_memories = char_data.get('key_memories', [])
            
            # Combine both memory types for debugging
            all_memories = []
            if memory_data:
                all_memories.append(memory_data)
            if key_memories:
                all_memories.extend(key_memories)
            
            debug_data['characters'][char_key] = {
                'name': char_data.get('name', 'Unknown'),
                'age': char_data.get('age', 'Unknown'),
                'gender': char_data.get('gender', 'Unknown'),
                'active': char_data.get('active', True),
                'memory': memory_data[:200] + '...' if len(memory_data) > 200 else memory_data,
                'key_memories': key_memories,
                'all_memories': all_memories,
                'physical': char_data.get('physical', {}),
                'intimate': char_data.get('intimate', {}),
                'personality': char_data.get('personality', {}),
                'role': char_data.get('role', ''),
                'sexual_growth_arc': char_data.get('sexual_growth_arc', '')
            }
        
        return jsonify(debug_data)
        
    except Exception as e:
        return jsonify({'error': f'Debug story failed: {str(e)}'}), 500

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

@app.route('/api/clear-scene', methods=['POST'])
def clear_scene():
    """Clear the current scene from session"""
    try:
        # Clear session history
        session['history'] = []
        
        # Clear any story state
        if 'current_story_id' in session:
            del session['current_story_id']
        
        print("🔍 Debug: Cleared scene from session")
        
        return jsonify({
            'success': True,
            'message': 'Scene cleared successfully'
        })
            
    except Exception as e:
        print(f"🔍 Debug: Error clearing scene: {e}")
        import traceback
        return jsonify({
            'error': f'Failed to clear scene: {str(e)}',
            'traceback': traceback.format_exc()
        })

@app.route('/api/scenes/<story_id>', methods=['GET'])
@require_auth
def get_story_scenes(story_id):
    """Get all scenes for a specific story"""
    try:
        # Get current user from session
        google_id = session.get('user_id')
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        if not DATABASE_AVAILABLE:
            return jsonify({'error': 'Database not available'}), 500
        
        # Ensure tables exist before querying
        if not ensure_tables_exist():
            return jsonify({'error': 'Database tables not available'}), 500
        
        # Get scenes for this story and user (case-insensitive)
        scenes = Scene.query.filter(
            Scene.story_id.ilike(story_id),
            Scene.user_id == google_id
        ).order_by(Scene.updated_at.desc()).all()
        
        scene_list = []
        for scene in scenes:
            scene_list.append({
                'id': scene.id,
                'title': scene.title,
                'message_count': scene.message_count,
                'created_at': scene.created_at.isoformat() if scene.created_at else None,
                'updated_at': scene.updated_at.isoformat() if scene.updated_at else None
            })
        
        print(f"🔍 Debug: Found {len(scene_list)} scenes for story {story_id} (user: {google_id})")
        print(f"🔍 Debug: Query was for story_id='{story_id}' (case-insensitive)")
        return jsonify({'scenes': scene_list})
        
    except Exception as e:
        print(f"🔍 Debug: Error getting story scenes: {e}")
        return jsonify({'error': f'Could not get scenes: {e}'}), 500

@app.route('/api/scenes/<story_id>/<int:scene_id>', methods=['GET'])
@require_auth
def get_story_scene(story_id, scene_id):
    """Get a specific scene"""
    try:
        # Get current user from session
        google_id = session.get('user_id')
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        if not DATABASE_AVAILABLE:
            return jsonify({'error': 'Database not available'}), 500
        
        # Ensure tables exist before querying
        if not ensure_tables_exist():
            return jsonify({'error': 'Database tables not available'}), 500
        
        # Get the specific scene
        scene = Scene.query.filter_by(
            id=scene_id,
            story_id=story_id,
            user_id=google_id
        ).first()
        
        if not scene:
            return jsonify({'error': 'Scene not found'}), 404
        
        # Update session with this scene
        session['history'] = scene.history
        session['current_story_id'] = story_id
        
        print(f"🔍 Debug: Loaded scene {scene_id} for story {story_id}")
        
        return jsonify({
            'success': True,
            'scene': {
                'id': scene.id,
                'title': scene.title,
                'history': scene.history,
                'message_count': scene.message_count,
                'created_at': scene.created_at.isoformat() if scene.created_at else None,
                'updated_at': scene.updated_at.isoformat() if scene.updated_at else None
            }
        })
        
    except Exception as e:
        print(f"🔍 Debug: Error getting scene: {e}")
        return jsonify({'error': f'Could not get scene: {e}'}), 500

@app.route('/api/active-session', methods=['GET'])
@require_auth
def get_active_session():
    """Get the user's active story and scene"""
    try:
        # Get current user from session
        google_id = session.get('user_id')
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        if not DATABASE_AVAILABLE:
            return jsonify({'error': 'Database not available'}), 500
        
        # Ensure tables exist
        if not ensure_tables_exist():
            return jsonify({'error': 'Database tables not available'}), 500
        
        # Get user's active story
        user = User.query.filter_by(google_id=google_id).first()
        if not user or not user.active_story_id:
            return jsonify({
                'success': True,
                'story_id': None,
                'scene_id': None,
                'message': 'No active story found'
            })
        
        # Get the active scene for this story
        active_scene = Scene.query.filter(
            Scene.story_id == user.active_story_id,
            Scene.user_id == google_id,
            Scene.is_active == True
        ).first()
        
        if not active_scene:
            # If no active scene, get the default scene
            story = Story.query.filter_by(story_id=user.active_story_id, user_id=google_id).first()
            if story and story.default_scene_id:
                active_scene = Scene.query.filter_by(id=story.default_scene_id).first()
                if active_scene:
                    # Set the default scene as active
                    active_scene.is_active = True
                    db.session.commit()
        
        return jsonify({
            'success': True,
            'story_id': user.active_story_id,
            'scene_id': active_scene.id if active_scene else None,
            'scene_title': active_scene.title if active_scene else None,
            'message': f'Active session: {user.active_story_id}'
        })
        
    except Exception as e:
        print(f"🔍 Debug: Error getting active session: {e}")
        return jsonify({'error': f'Could not get active session: {e}'}), 500

@app.route('/api/set-active-story', methods=['POST'])
@require_auth
def set_active_story():
    """Set a story as the user's active story and load its active scene"""
    try:
        google_id = session.get('user_id')
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        if not DATABASE_AVAILABLE or not ensure_tables_exist():
            return jsonify({'error': 'Database not available'}), 500
        
        data = request.get_json()
        story_id = data.get('story_id')
        
        if not story_id:
            return jsonify({'error': 'Story ID required'}), 400
        
        # Verify the story exists and belongs to the user
        story = Story.query.filter_by(story_id=story_id, user_id=google_id).first()
        if not story:
            return jsonify({'error': f'Story not found: {story_id}'}), 404
        
        # Set the story as active for the user
        user = User.query.filter_by(google_id=google_id).first()
        if user:
            user.active_story_id = story_id
            print(f"🔍 Debug: Set {story_id} as active story for user {google_id}")
        
        # Find or create the active scene for this story
        active_scene = Scene.query.filter(
            Scene.story_id == story_id,
            Scene.user_id == google_id,
            Scene.is_active == True
        ).first()
        
        if not active_scene:
            # Use the default scene if no active scene exists
            if story.default_scene_id:
                active_scene = Scene.query.filter_by(id=story.default_scene_id).first()
                if active_scene:
                    active_scene.is_active = True
                    print(f"🔍 Debug: Set default scene {active_scene.id} as active for story {story_id}")
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'story_id': story_id,
            'scene_id': active_scene.id if active_scene else None,
            'scene_title': active_scene.title if active_scene else None,
            'message': f'Active story set to {story_id}'
        })
    except Exception as e:
        print(f"🔍 Debug: Error setting active story: {e}")
        return jsonify({'error': f'Could not set active story: {e}'}), 500

@app.route('/api/clear-active-scene', methods=['POST'])
@require_auth
def clear_active_scene():
    """Clear the active scene and reset to the Opening scene"""
    try:
        google_id = session.get('user_id')
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        if not DATABASE_AVAILABLE or not ensure_tables_exist():
            return jsonify({'error': 'Database not available'}), 500
        
        user = User.query.filter_by(google_id=google_id).first()
        if not user or not user.active_story_id:
            return jsonify({'error': 'No active story found'}), 404
        
        # Get the story and its default scene
        story = Story.query.filter_by(story_id=user.active_story_id, user_id=google_id).first()
        if not story or not story.default_scene_id:
            return jsonify({'error': 'No default scene found for active story'}), 404
        
        # Clear all active scenes for this story
        Scene.query.filter(
            Scene.story_id == user.active_story_id,
            Scene.user_id == google_id,
            Scene.is_active == True
        ).update({'is_active': False})
        
        # Set the default scene as active
        default_scene = Scene.query.filter_by(id=story.default_scene_id).first()
        if default_scene:
            default_scene.is_active = True
            default_scene.history = []  # Clear the history
            default_scene.message_count = 0
            print(f"🔍 Debug: Reset to Opening scene {default_scene.id} for story {user.active_story_id}")
        
        db.session.commit()
        
        # Generate the opening content from the story
        story_content = story.content
        opener_text = story_content.get('opener_text', '')
        
        if opener_text:
            # Clear session history and add opener content
            session['history'] = []
            session['history'].append({"role": "user", "content": opener_text})
            print(f"🔍 Debug: Added opener content to session for cleared scene")
            
            # Reset the story state manager to clear old state
            try:
                state_manager = StoryStateManager()
                state_manager.reset_state()
                print(f"🔍 Debug: Reset story state manager to clear old state")
            except Exception as e:
                print(f"🔍 Debug: Error resetting story state manager: {e}")
            
            # Update the active scene with the opener content
            default_scene.history = session['history']
            default_scene.message_count = 1
            db.session.commit()
            
            print(f"🔍 Debug: Updated default scene {default_scene.id} with opener content")
            print(f"🔍 Debug: Default scene history length: {len(default_scene.history)}")
            print(f"🔍 Debug: Default scene is_active: {default_scene.is_active}")
            
            # Also clear the current story ID from session to force reload
            if 'story_id' in session:
                del session['story_id']
            if 'current_story_id' in session:
                del session['current_story_id']
            
            print(f"🔍 Debug: Cleared story ID from session to force reload")
            
            return jsonify({
                'success': True,
                'story_id': user.active_story_id,
                'scene_id': default_scene.id if default_scene else None,
                'scene_title': default_scene.title if default_scene else None,
                'message': f'Active scene cleared and reset to Opening',
                'opener_content': opener_text,
                'ai_response': None,
                'response_type': 'system'
            })
        else:
            return jsonify({
                'success': True,
                'story_id': user.active_story_id,
                'scene_id': default_scene.id if default_scene else None,
                'scene_title': default_scene.title if default_scene else None,
                'message': f'Active scene cleared and reset to Opening (no opener content)'
            })
    except Exception as e:
        print(f"🔍 Debug: Error clearing active scene: {e}")
        return jsonify({'error': f'Could not clear active scene: {e}'}), 500

@app.route('/api/current-story-id', methods=['GET'])
@require_auth
def get_current_story_id_api():
    """Get the current story ID from session (legacy endpoint)"""
    try:
        story_id = get_current_story_id()
        if story_id:
            return jsonify({
                'success': True,
                'story_id': story_id
            })
        else:
            return jsonify({
                'success': False,
                'message': 'No story currently loaded'
            })
    except Exception as e:
        print(f"🔍 Debug: Error getting current story ID: {e}")
        return jsonify({'error': f'Could not get current story ID: {e}'}), 500

@app.route('/api/scenes/<story_id>', methods=['POST'])
@require_auth
def save_story_scene(story_id):
    """Save a scene for a specific story"""
    try:
        data = request.get_json()
        if not data or 'history' not in data:
            return jsonify({'error': 'No scene history provided'}), 400
        
        # Get current user from session
        google_id = session.get('user_id')
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        if not DATABASE_AVAILABLE:
            return jsonify({'error': 'Database not available'}), 500
        
        # Ensure tables exist before querying
        if not ensure_tables_exist():
            return jsonify({'error': 'Database tables not available'}), 500
        
        history = data['history']
        title = data.get('title', f'Scene {datetime.now().strftime("%Y-%m-%d %H:%M")}')
        
        # Create new scene
        new_scene = Scene(
            story_id=story_id,
            user_id=google_id,
            title=title,
            history=history,
            message_count=len(history)
        )
        
        db.session.add(new_scene)
        db.session.commit()
        
        print(f"🔍 Debug: Saved scene for story '{story_id}' with {len(history)} messages (user: {google_id})")
        print(f"🔍 Debug: Scene ID: {new_scene.id}, Title: '{title}'")
        
        return jsonify({
            'success': True,
            'message': f'Scene saved successfully',
            'scene_id': new_scene.id
        })
        
    except Exception as e:
        print(f"🔍 Debug: Error saving scene: {e}")
        return jsonify({'error': f'Could not save scene: {e}'}), 500

@app.route('/api/debug-payload', methods=['GET'])
@require_auth
def get_debug_payload():
    """Get the last AI payloads for debugging"""
    try:
        google_id = session.get('user_id')
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        user_payloads = last_ai_payloads.get(google_id, {})
        
        return jsonify({
            'success': True,
            'payloads': user_payloads
        })
        
    except Exception as e:
        print(f"🔍 Debug: Error getting debug payload: {e}")
        return jsonify({'error': f'Could not get debug payload: {e}'}), 500

@app.route('/api/export-debug-data', methods=['POST'])
@require_auth
def export_debug_data():
    """Export complete debug data including chat history and payloads"""
    try:
        google_id = session.get('user_id')
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        # Get current session history
        session_history = session.get('history', [])
        
        # Get user payloads
        user_payloads = last_ai_payloads.get(google_id, {})
        
        # Get story points if available
        story_points = get_story_points(google_id) if google_id else []
        
        # Get current story state
        try:
            state_manager = StoryStateManager()
            current_state = state_manager.get_current_state()
        except Exception as e:
            current_state = {"error": f"Could not load state: {e}"}
        
        # Generate timestamp for filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"debug_export_{timestamp}.txt"
        
        # Build export content
        export_content = []
        export_content.append("=" * 80)
        export_content.append("GROK PLAYGROUND DEBUG EXPORT")
        export_content.append("=" * 80)
        export_content.append(f"Export Date: {datetime.now().isoformat()}")
        export_content.append(f"User ID: {google_id}")
        export_content.append("")
        
        # Session History
        export_content.append("=" * 80)
        export_content.append("CHAT HISTORY")
        export_content.append("=" * 80)
        if session_history:
            for i, message in enumerate(session_history):
                export_content.append(f"\n--- Message {i+1} ---")
                export_content.append(f"Role: {message.get('role', 'unknown')}")
                export_content.append(f"Content: {message.get('content', '')}")
                export_content.append("")
        else:
            export_content.append("No chat history found.")
        export_content.append("")
        
        # Story Points
        export_content.append("=" * 80)
        export_content.append("STORY POINTS")
        export_content.append("=" * 80)
        if story_points:
            for i, point in enumerate(story_points):
                export_content.append(f"{i+1}. {point}")
        else:
            export_content.append("No story points found.")
        export_content.append("")
        
        # Current State
        export_content.append("=" * 80)
        export_content.append("CURRENT STORY STATE")
        export_content.append("=" * 80)
        export_content.append(json.dumps(current_state, indent=2))
        export_content.append("")
        
        # AI Payloads
        export_content.append("=" * 80)
        export_content.append("AI PAYLOADS")
        export_content.append("=" * 80)
        if user_payloads:
            for payload_type, payload_data in user_payloads.items():
                export_content.append(f"\n--- {payload_type.upper()} ---")
                export_content.append(f"Timestamp: {payload_data.get('timestamp', 'unknown')}")
                export_content.append(f"Payload Size: {payload_data.get('payload_size', 'unknown')} chars")
                export_content.append("")
                
                # Payload (Input)
                export_content.append("PAYLOAD (INPUT):")
                export_content.append(json.dumps(payload_data.get('payload', {}), indent=2))
                export_content.append("")
                
                # Response (Output)
                if payload_data.get('response'):
                    export_content.append("RESPONSE (OUTPUT):")
                    export_content.append(payload_data['response'])
                    export_content.append("")
                
                # Usage Info
                if payload_data.get('usage'):
                    export_content.append("USAGE INFO:")
                    export_content.append(json.dumps(payload_data['usage'], indent=2))
                    export_content.append("")
                
                # Finish Reason
                if payload_data.get('finish_reason'):
                    export_content.append(f"FINISH REASON: {payload_data['finish_reason']}")
                    export_content.append("")
                
                export_content.append("-" * 60)
        else:
            export_content.append("No AI payloads found.")
        export_content.append("")
        
        # Server Info
        export_content.append("=" * 80)
        export_content.append("SERVER INFORMATION")
        export_content.append("=" * 80)
        export_content.append(f"Python Version: {sys.version}")
        export_content.append(f"Current Directory: {os.getcwd()}")
        export_content.append(f"TTS Enabled: {tts.enabled}")
        export_content.append(f"TTS Status: {tts.get_mode_display()}")
        export_content.append(f"API Key Set: {'Yes' if os.getenv('XAI_API_KEY') else 'No'}")
        export_content.append(f"Model: {os.getenv('XAI_MODEL', 'grok-3')}")
        export_content.append("")
        
        # Join all content
        full_content = "\n".join(export_content)
        
        # Create response with file download
        from flask import Response
        response = Response(
            full_content,
            mimetype='text/plain',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'text/plain; charset=utf-8'
            }
        )
        
        print(f"🔍 Debug: Generated debug export file: {filename} ({len(full_content)} chars)")
        return response
        
    except Exception as e:
        print(f"🔍 Debug: Error exporting debug data: {e}")
        import traceback
        print(f"🔍 Debug: Export error traceback: {traceback.format_exc()}")
        return jsonify({'error': f'Could not export debug data: {e}'}), 500

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
        google_id = session.get('user_id')
        
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        print(f"🔍 Debug: Using Google ID: {google_id}")
        user_id = google_id
        
        if not DATABASE_AVAILABLE:
            return jsonify({'error': 'Database not available'}), 500
        
        # Ensure tables exist before querying
        if not ensure_tables_exist():
            return jsonify({'error': 'Database tables not available'}), 500
        
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
    except Exception as e:
        print(f"🔍 Debug: Error listing story files: {e}")
        return jsonify({'error': f'Could not list story files: {e}'}), 500

@app.route('/api/story-files/<story_id>', methods=['GET'])
@require_auth
def get_story_file(story_id):
    """Get a specific story from database"""
    try:
        # Get current user from session
        google_id = session.get('user_id')
        
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        print(f"🔍 Debug: Using Google ID: {google_id}")
        user_id = google_id
        
        if not DATABASE_AVAILABLE:
            return jsonify({'error': 'Database not available'}), 500
        
        # Ensure tables exist before querying
        if not ensure_tables_exist():
            return jsonify({'error': 'Database tables not available'}), 500
        
        # Get story from database (case-insensitive)
        story = Story.query.filter_by(user_id=user_id).filter(Story.story_id.ilike(story_id)).first()
        
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
    except Exception as e:
        print(f"🔍 Debug: Error reading story {story_id}: {e}")
        return jsonify({'error': f'Could not read story: {e}'}), 500

@app.route('/api/story-files/<story_id>', methods=['PATCH'])
@require_auth
def update_story_metadata(story_id):
    """Update story metadata (like public/private status)"""
    try:
        # Get current user from session
        google_id = session.get('user_id')
        
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        if not DATABASE_AVAILABLE:
            return jsonify({'error': 'Database not available'}), 500
        
        # Ensure tables exist before querying
        if not ensure_tables_exist():
            return jsonify({'error': 'Database tables not available'}), 500
        
        # Get story from database
        story = Story.query.filter_by(story_id=story_id, user_id=google_id).first()
        
        if not story:
            return jsonify({'error': f'Story not found: {story_id}'}), 404
        
        # Update story metadata
        data = request.get_json()
        if 'is_public' in data:
            story.is_public = data['is_public']
        if 'title' in data:
            story.title = data['title']
        
        story.updated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Story updated successfully'
        })
        
    except Exception as e:
        print(f"🔍 Debug: Error updating story {story_id}: {e}")
        return jsonify({'error': f'Could not update story: {e}'}), 500

@app.route('/api/story-files/<story_id>', methods=['DELETE'])
@require_auth
def delete_story(story_id):
    """Delete a story"""
    try:
        # Get current user from session
        google_id = session.get('user_id')
        
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        if not DATABASE_AVAILABLE:
            return jsonify({'error': 'Database not available'}), 500
        
        # Ensure tables exist before querying
        if not ensure_tables_exist():
            return jsonify({'error': 'Database tables not available'}), 500
        
        # Get story from database
        story = Story.query.filter_by(story_id=story_id, user_id=google_id).first()
        
        if not story:
            return jsonify({'error': f'Story not found: {story_id}'}), 404
        
        # Delete the story
        db.session.delete(story)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Story deleted successfully'
        })
        
    except Exception as e:
        print(f"🔍 Debug: Error deleting story {story_id}: {e}")
        return jsonify({'error': f'Could not delete story: {e}'}), 500

@app.route('/dashboard')
@require_auth
def dashboard():
    """Serve the user dashboard"""
    return render_template('dashboard.html')

@app.route('/upload-story')
@require_auth
def upload_story_page():
    """Serve the upload story page"""
    return render_template('upload_story.html')

@app.route('/api/upload-story', methods=['POST'])
@require_auth
def upload_story_from_file():
    """Upload a story from a local file to the database"""
    try:
        story_filename = request.json.get('filename')
        if not story_filename:
            return jsonify({'error': 'Filename required'}), 400
        
        # Get current user from session
        google_id = session.get('user_id')
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        if not DATABASE_AVAILABLE:
            return jsonify({'error': 'Database not available'}), 500
        
        # Ensure tables exist
        if not ensure_tables_exist():
            return jsonify({'error': 'Database tables not available'}), 500
        
        # Read the story file
        if not os.path.exists(story_filename):
            return jsonify({'error': f'Story file not found: {story_filename}'}), 404
        
        with open(story_filename, 'r', encoding='utf-8') as f:
            story_data = json.load(f)
        
        story_id = story_data.get('story_id', 'unknown')
        title = story_data.get('title', story_id)
        
        # Check if story already exists
        existing_story = Story.query.filter_by(story_id=story_id, user_id=google_id).first()
        
        if existing_story:
            # Update existing story
            existing_story.title = title
            existing_story.content = story_data
            existing_story.updated_at = datetime.utcnow()
            db.session.commit()
            
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
                user_id=google_id,
                content=story_data,
                is_public=False
            )
            db.session.add(new_story)
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': f'Story uploaded: {title}',
                'story_id': story_id,
                'action': 'created'
            })
            
    except Exception as e:
        print(f"🔍 Debug: Error uploading story: {e}")
        return jsonify({'error': f'Could not upload story: {e}'}), 500

@app.route('/api/story-files', methods=['POST'])
@require_auth
def save_story_file():
    """Save a story file"""
    try:
        story_data = request.get_json()
        
        if not story_data or 'story_id' not in story_data:
            return jsonify({'error': 'Invalid story data'}), 400
        
        # Get current user from session
        google_id = session.get('user_id')
        
        if not google_id:
            return jsonify({'error': 'User not found in session'}), 401
        
        print(f"🔍 Debug: Using Google ID: {google_id}")
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
        
        if not DATABASE_AVAILABLE:
            return jsonify({'error': 'Database not available'}), 500
        
        # Ensure tables exist before querying
        if not ensure_tables_exist():
            return jsonify({'error': 'Database tables not available'}), 500
        
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
            db.session.flush()  # Flush to get the story ID
            
            # Create "Opening" scene for the new story
            opening_scene = Scene(
                story_id=story_id,
                user_id=user_id,
                title="Opening",
                history=[],  # Empty history - just the story context
                message_count=0,
                is_default=True,
                is_active=True
            )
            db.session.add(opening_scene)
            db.session.flush()  # Flush to get the scene ID
            
            # Update story with default scene reference
            new_story.default_scene_id = opening_scene.id
            
            # Set this story as the user's active story
            user = User.query.filter_by(google_id=user_id).first()
            if user:
                user.active_story_id = story_id
                print(f"🔍 Debug: Set {story_id} as active story for user {user_id}")
            
            db.session.commit()
            
            print(f"🔍 Debug: Story saved to database: {story_id} by user {user_id}")
            print(f"🔍 Debug: Created Opening scene (ID: {opening_scene.id}) for story {story_id}")
            
            return jsonify({
                'success': True,
                'message': f'Story saved: {title}',
                'story_id': story_id,
                'action': 'created'
            })
    except Exception as e:
        print(f"🔍 Debug: Error saving story file: {e}")
        return jsonify({'error': f'Could not save story file: {e}'}), 500

def ensure_tables_exist():
    """Ensure database tables exist with correct schema"""
    if not DATABASE_AVAILABLE:
        return False
        
    try:
        with app.app_context():
            # Test database connection first
            from sqlalchemy import text
            try:
                with db.engine.connect() as conn:
                    conn.execute(text('SELECT 1'))
            except Exception as conn_error:
                print(f"❌ Database connection failed: {conn_error}")
                return False
            
            # Check if tables exist first
            from sqlalchemy import inspect
            inspector = inspect(db.engine)
            existing_tables = inspector.get_table_names()
            
            # Check if we have the old 'conversations' table that needs to be migrated to 'scenes'
            if 'conversations' in existing_tables and 'scenes' not in existing_tables:
                print("🔄 Migrating 'conversations' table to 'scenes' table...")
                db.drop_all()
                db.create_all()
                print("✅ Database tables migrated from 'conversations' to 'scenes'")
                # Force connection refresh to ensure new schema is used
                db.engine.dispose()
                return True
            
            if 'stories' in existing_tables and 'users' in existing_tables and 'scenes' in existing_tables:
                # Tables exist, check if schema is correct
                try:
                    # Check stories table for new columns
                    story_columns = [col['name'] for col in inspector.get_columns('stories')]
                    user_columns = [col['name'] for col in inspector.get_columns('users')]
                    scene_columns = [col['name'] for col in inspector.get_columns('scenes')]
                    
                    # Check for all required new columns
                    required_story_cols = ['default_scene_id']
                    required_user_cols = ['active_story_id']
                    required_scene_cols = ['is_default', 'is_active']
                    
                    missing_cols = []
                    if not all(col in story_columns for col in required_story_cols):
                        missing_cols.extend([f'stories.{col}' for col in required_story_cols if col not in story_columns])
                    if not all(col in user_columns for col in required_user_cols):
                        missing_cols.extend([f'users.{col}' for col in required_user_cols if col not in user_columns])
                    if not all(col in scene_columns for col in required_scene_cols):
                        missing_cols.extend([f'scenes.{col}' for col in required_scene_cols if col not in scene_columns])
                    
                    if missing_cols:
                        print(f"⚠️ Schema mismatch: Missing columns {missing_cols}")
                        print("🔄 Schema mismatch detected, recreating tables...")
                        db.drop_all()
                        db.create_all()
                        print("✅ Database tables recreated with correct schema")
                        # Force connection refresh to ensure new schema is used
                        db.engine.dispose()
                        return True
                    else:
                        print("✅ Database tables exist with correct schema")
                        return True
                except Exception as schema_error:
                    print(f"❌ Schema check failed: {schema_error}")
                    # Try to recreate tables
                    print("🔄 Attempting to recreate tables due to schema error...")
                    db.drop_all()
                    db.create_all()
                    print("✅ Database tables recreated")
                    # Force connection refresh to ensure new schema is used
                    db.engine.dispose()
                    return True
            else:
                # Tables don't exist, create them
                print("🔄 Creating missing database tables...")
                db.create_all()
                print("✅ Database tables created")
                return True
                
    except Exception as e:
        print(f"❌ Failed to ensure tables exist: {e}")
        import traceback
        print(f"Database error traceback: {traceback.format_exc()}")
        
        # Try one more time with a fresh connection
        try:
            print("🔄 Attempting database recovery...")
            with app.app_context():
                db.drop_all()
                db.create_all()
                print("✅ Database recovery successful")
                # Force connection refresh to ensure new schema is used
                db.engine.dispose()
                return True
        except Exception as recovery_error:
            print(f"❌ Database recovery failed: {recovery_error}")
            return False

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
