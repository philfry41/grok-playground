import os
import tempfile
import subprocess
import requests
from elevenlabs import ElevenLabs

class TTSHelper:
    def __init__(self):
        self.api_key = os.getenv("ELEVENLABS_API_KEY")
        # TTS modes: "off", "tts" (auto-play), "save" (auto-save)
        # Load mode from file for persistence across worker restarts
        self.mode = self._load_tts_mode()
        
        # Load voice ID from file for persistence across worker restarts
        self.voice_id = self._load_voice_id()
        
        self.volume = float(os.getenv("ELEVENLABS_VOLUME", "0.5"))
        self.max_tts_length = int(os.getenv("ELEVENLABS_MAX_LENGTH", "5000"))  # 0 = no limit
        
        # Voice model cache to avoid repeated API calls
        self._voice_models_cache = {}
        
        # Initialize client if API key is available
        if self.api_key:
            try:
                self.client = ElevenLabs(api_key=self.api_key)
                print(f"🎤 TTS API key found - ready to enable")
            except Exception as e:
                print(f"⚠️ TTS API key invalid: {e}")
                self.api_key = None
        else:
            print("🔇 TTS disabled - no API key set")
    
    @property
    def enabled(self):
        """TTS is enabled if we have an API key and mode is not 'off'"""
        return bool(self.api_key) and self.mode != "off"
    
    @property
    def auto_save(self):
        """Auto-save is enabled if mode is 'save'"""
        return self.mode == "save"
    
    def get_available_voices(self):
        """Get list of available voices with model information"""
        if not self.enabled:
            return []
        
        try:
            # Use direct API call to get detailed voice information
            headers = {"xi-api-key": self.api_key}
            response = requests.get("https://api.elevenlabs.io/v1/voices", headers=headers)
            response.raise_for_status()
            
            voices_data = response.json()
            voices = []
            
            for voice in voices_data.get("voices", []):
                voice_id = voice.get("voice_id")
                name = voice.get("name", "Unknown")
                
                # Get best available model for this voice
                best_model = self._get_best_model_for_voice(voice)
                
                voices.append({
                    "voice_id": voice_id,
                    "name": name,
                    "model": best_model,
                    "has_flash_v2_5": best_model == "eleven_flash_v2_5"
                })
            
            return voices
        except Exception as e:
            print(f"⚠️ Could not fetch voices: {e}")
            return []
    
    def _get_best_model_for_voice(self, voice_data):
        """Get the best available model for a voice, preferring FLASH V2.5"""
        if not voice_data or "fine_tuning" not in voice_data:
            return "eleven_monolingual_v1"  # Default fallback
        
        fine_tuning = voice_data.get("fine_tuning", {})
        state = fine_tuning.get("state", {})
        
        # Priority order: FLASH V2.5 > FLASH V2 > TURBO V2.5 > TURBO V2 > Multilingual V2 > Default
        model_priority = [
            "eleven_flash_v2_5",
            "eleven_flash_v2", 
            "eleven_turbo_v2_5",
            "eleven_turbo_v2",
            "eleven_multilingual_v2",
            "eleven_v2_flash",
            "eleven_v2_5_flash"
        ]
        
        for model in model_priority:
            if state.get(model) == "fine_tuned":
                return model
        
        # Fallback to default model
        return "eleven_monolingual_v1"
    
    def get_voice_model(self, voice_id=None):
        """Get the best model for a specific voice"""
        if voice_id is None:
            voice_id = self.voice_id
        
        # Check cache first
        if voice_id in self._voice_models_cache:
            return self._voice_models_cache[voice_id]
        
        try:
            # Fetch voice details
            headers = {"xi-api-key": self.api_key}
            response = requests.get(f"https://api.elevenlabs.io/v1/voices/{voice_id}", headers=headers)
            response.raise_for_status()
            
            voice_data = response.json()
            best_model = self._get_best_model_for_voice(voice_data)
            
            # Cache the result
            self._voice_models_cache[voice_id] = best_model
            
            print(f"🔍 Debug: Voice {voice_id} using model: {best_model}")
            return best_model
            
        except Exception as e:
            print(f"⚠️ Could not get model for voice {voice_id}: {e}")
            return "eleven_monolingual_v1"  # Default fallback
    
    def _load_voice_id(self):
        """Load voice ID from file for persistence"""
        try:
            if os.path.exists("tts_voice_id.txt"):
                with open("tts_voice_id.txt", "r") as f:
                    voice_id = f.read().strip()
                    if voice_id:
                        print(f"🔍 Debug: Loaded voice ID from file: {voice_id}")
                        return voice_id
                    else:
                        print(f"🔍 Debug: Voice ID file is empty, using default")
            else:
                print(f"🔍 Debug: No voice ID file found, using default")
        except Exception as e:
            print(f"🔍 Debug: Error loading voice ID: {e}")
        
        # Default to environment variable or fallback
        return os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")  # Adam voice
    
    def _save_voice_id(self, voice_id):
        """Save voice ID to file for persistence"""
        try:
            print(f"🔍 Debug: Saving voice ID to file: {voice_id}")
            with open("tts_voice_id.txt", "w") as f:
                f.write(voice_id)
            print(f"🔍 Debug: Voice ID saved to file successfully")
        except Exception as e:
            print(f"🔍 Debug: Error saving voice ID: {e}")
    
    def set_voice(self, voice_id):
        """Change the voice ID"""
        self.voice_id = voice_id
        print(f"🎤 Voice changed to: {voice_id}")
        
        # Save voice ID to file for persistence
        self._save_voice_id(voice_id)
        
        # Get and log the model for this voice
        model = self.get_voice_model(voice_id)
        print(f"🎤 Voice {voice_id} will use model: {model}")
    
    def cycle_mode(self):
        """Cycle through TTS modes: off -> tts -> save -> off"""
        if not self.api_key:
            print("🔇 TTS disabled - no API key set")
            return "off"
        
        if self.mode == "off":
            self.mode = "tts"
            print("🎤 TTS enabled (auto-play mode)")
        elif self.mode == "tts":
            self.mode = "save"
            print("💾 TTS enabled (auto-save mode)")
        else:  # save
            self.mode = "off"
            print("🔇 TTS disabled")
        
        # Save mode to file for persistence across worker restarts
        self._save_tts_mode(self.mode)
        print(f"🔍 Debug: TTS mode after cycle: {self.mode}")
        
        return self.mode
    
    def _load_tts_mode(self):
        """Load TTS mode from file for persistence"""
        try:
            if os.path.exists("tts_mode.txt"):
                with open("tts_mode.txt", "r") as f:
                    mode = f.read().strip()
                    if mode in ["off", "tts", "save"]:
                        print(f"🔍 Debug: Loaded TTS mode from file: {mode}")
                        return mode
                    else:
                        print(f"🔍 Debug: Invalid TTS mode in file: {mode}")
            else:
                print(f"🔍 Debug: No TTS mode file found, using default: tts (auto-play)")
        except Exception as e:
            print(f"🔍 Debug: Error loading TTS mode: {e}")
        
        return "tts"  # Default to auto-play mode for TTS
    
    def _save_tts_mode(self, mode):
        """Save TTS mode to file for persistence"""
        try:
            print(f"🔍 Debug: Attempting to save TTS mode: {mode}")
            with open("tts_mode.txt", "w") as f:
                f.write(mode)
            print(f"🔍 Debug: Saved TTS mode to file: {mode}")
            
            # Verify the file was created
            if os.path.exists("tts_mode.txt"):
                with open("tts_mode.txt", "r") as f:
                    saved_mode = f.read().strip()
                print(f"🔍 Debug: Verified TTS mode file contains: {saved_mode}")
            else:
                print(f"🔍 Debug: Warning: TTS mode file was not created")
        except Exception as e:
            print(f"🔍 Debug: Error saving TTS mode: {e}")
            import traceback
            print(f"🔍 Debug: Save error traceback: {traceback.format_exc()}")
    
    def get_mode_display(self):
        """Get human-readable mode description"""
        if not self.api_key:
            return "No API Key"
        elif self.mode == "off":
            return "Disabled"
        elif self.mode == "tts":
            return "Auto-Play"
        else:  # save
            return "Auto-Save"
    
    def speak(self, text, save_audio=False):
        """Generate and save/play TTS audio"""
        if not self.enabled or not text.strip():
            return
        
        try:
            # Clean text for TTS (remove markdown, etc.)
            clean_text = self._clean_text_for_tts(text)
            
            # Get the best model for this voice
            model_id = self.get_voice_model(self.voice_id)
            print(f"🔍 Debug: Using model {model_id} for voice {self.voice_id}")
            print(f"🔍 Debug: TTS speak() called with voice_id: {self.voice_id}")
            
            # Generate audio using the new API with error handling
            print(f"🔍 Debug: Calling ElevenLabs API for text length: {len(clean_text)}")
            try:
                audio = self.client.text_to_speech.convert(
                    text=clean_text,
                    voice_id=self.voice_id,
                    model_id=model_id
                )
                print(f"🔍 Debug: ElevenLabs API call completed successfully with model {model_id}")
            except Exception as api_error:
                print(f"🔍 Debug: ElevenLabs API error: {api_error}")
                import traceback
                print(f"🔍 Debug: API error traceback: {traceback.format_exc()}")
                return None
            
            # Determine if we should save or play based on save_audio parameter
            should_save = save_audio
            
            if should_save:
                # Create audio directory if it doesn't exist
                audio_dir = "audio"
                print(f"🔍 Debug: Creating audio directory: {audio_dir}")
                try:
                    os.makedirs(audio_dir, exist_ok=True)
                    print(f"🔍 Debug: Audio directory ready: {os.path.exists(audio_dir)}")
                except Exception as dir_error:
                    print(f"🔍 Debug: Error creating audio directory: {dir_error}")
                    return None
                
                # Save to a timestamped file in audio directory
                import datetime
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"grok_response_{timestamp}.mp3"
                filepath = os.path.join(audio_dir, filename)
                print(f"🔍 Debug: Saving audio to: {filepath}")
                
                try:
                    # Debug the audio data
                    print(f"🔍 Debug: Audio data type: {type(audio)}")
                    print(f"🔍 Debug: Audio data length: {len(audio) if hasattr(audio, '__len__') else 'Unknown'}")
                    
                    # Convert audio to bytes if it's not already
                    try:
                        if hasattr(audio, 'read'):
                            # It's a file-like object
                            audio_bytes = audio.read()
                            print(f"🔍 Debug: Read {len(audio_bytes)} bytes from audio stream")
                        elif hasattr(audio, '__iter__') and not isinstance(audio, (bytes, str)):
                            # It's a generator or iterable
                            audio_bytes = b''.join(chunk for chunk in audio)
                            print(f"🔍 Debug: Consumed generator into {len(audio_bytes)} bytes")
                        elif isinstance(audio, (list, tuple)):
                            # It's a list of chunks
                            audio_bytes = b''.join(chunk for chunk in audio)
                            print(f"🔍 Debug: Combined {len(audio)} chunks into {len(audio_bytes)} bytes")
                        else:
                            # Assume it's already bytes
                            audio_bytes = audio
                            print(f"🔍 Debug: Using audio as bytes: {len(audio_bytes)} bytes")
                    except Exception as convert_error:
                        print(f"🔍 Debug: Error converting audio data: {convert_error}")
                        import traceback
                        print(f"🔍 Debug: Convert error traceback: {traceback.format_exc()}")
                        return None
                    
                    # Validate that we have actual audio data
                    if len(audio_bytes) < 100:
                        print(f"🔍 Debug: Warning: Audio file seems too small ({len(audio_bytes)} bytes)")
                    
                    # Check for MP3 header
                    if audio_bytes.startswith(b'ID3') or audio_bytes.startswith(b'\xff\xfb'):
                        print(f"🔍 Debug: Valid MP3 header detected")
                    else:
                        print(f"🔍 Debug: Warning: No valid MP3 header detected")
                        print(f"🔍 Debug: First 20 bytes: {audio_bytes[:20]}")
                    
                    with open(filepath, "wb") as f:
                        f.write(audio_bytes)
                    
                    print(f"💾 Audio saved to: {filepath}")
                    print(f"🔍 Debug: File exists after save: {os.path.exists(filepath)}")
                    print(f"🔍 Debug: File size: {os.path.getsize(filepath)} bytes")
                    
                    # Verify the file is valid
                    if os.path.getsize(filepath) == 0:
                        print(f"🔍 Debug: Error: File is empty after save")
                        return None
                    
                    return filepath
                except Exception as save_error:
                    print(f"🔍 Debug: Error saving audio file: {save_error}")
                    import traceback
                    print(f"🔍 Debug: Save error traceback: {traceback.format_exc()}")
                    return None
            else:
                # Play audio directly
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    for chunk in audio:
                        f.write(chunk)
                    f.flush()
                    self._play_audio(f.name)
                    # Clean up temp file
                    os.unlink(f.name)
                    
        except Exception as e:
            print(f"⚠️ TTS error: {e}")
    
    def _clean_text_for_tts(self, text):
        """Clean text for better TTS quality"""
        # Remove markdown formatting
        import re
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
        text = re.sub(r'\*(.*?)\*', r'\1', text)      # Italic
        text = re.sub(r'`(.*?)`', r'\1', text)        # Code
        text = re.sub(r'#{1,6}\s+', '', text)         # Headers
        text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)  # Links
        
        # Remove excessive whitespace
        text = re.sub(r'\n+', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        
        # Limit length for TTS (avoid extremely long audio)
        # 5000 characters ≈ 5-7 minutes of audio
        if self.max_tts_length > 0 and len(text) > self.max_tts_length:
            text = text[:self.max_tts_length] + "..."
        
        return text.strip()
    
    def _play_audio(self, file_path):
        """Play audio file using system default player"""
        try:
            # Try different audio players
            players = ['afplay', 'mpg123', 'mpv', 'vlc']
            
            for player in players:
                try:
                    if player == 'afplay':
                        # macOS built-in player
                        subprocess.run([player, file_path], check=True, 
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
                    else:
                        # Other players
                        subprocess.run([player, file_path], check=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        return
                except (subprocess.CalledProcessError, FileNotFoundError):
                    continue
            
            print("⚠️ No audio player found. Install afplay (macOS), mpg123, mpv, or vlc")
            
        except Exception as e:
            print(f"⚠️ Audio playback error: {e}")

# Global TTS instance
tts = TTSHelper()
