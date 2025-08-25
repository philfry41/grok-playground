import os
import tempfile
import subprocess
from elevenlabs import ElevenLabs

class TTSHelper:
    def __init__(self):
        self.api_key = os.getenv("ELEVENLABS_API_KEY")
        # TTS modes: "off", "tts" (auto-play), "save" (auto-save)
        self.mode = "off"  # Default to disabled
        self.voice_id = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")  # Adam voice
        self.volume = float(os.getenv("ELEVENLABS_VOLUME", "0.5"))
        self.max_tts_length = int(os.getenv("ELEVENLABS_MAX_LENGTH", "5000"))  # 0 = no limit
        
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
        """Get list of available voices"""
        if not self.enabled:
            return []
        
        try:
            available_voices = self.client.voices.get_all()
            return [(v.voice_id, v.name) for v in available_voices.voices]
        except Exception as e:
            print(f"⚠️ Could not fetch voices: {e}")
            return []
    
    def set_voice(self, voice_id):
        """Change the voice ID"""
        self.voice_id = voice_id
        print(f"🎤 Voice changed to: {voice_id}")
    
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
        
        return self.mode
    
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
            
            # Generate audio using the new API
            audio = self.client.text_to_speech.convert(
                text=clean_text,
                voice_id=self.voice_id,
                model_id="eleven_monolingual_v1"
            )
            
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
                    if hasattr(audio, 'read'):
                        # It's a file-like object
                        audio_bytes = audio.read()
                        print(f"🔍 Debug: Read {len(audio_bytes)} bytes from audio stream")
                    elif isinstance(audio, (list, tuple)):
                        # It's a list of chunks
                        audio_bytes = b''.join(chunk for chunk in audio)
                        print(f"🔍 Debug: Combined {len(audio)} chunks into {len(audio_bytes)} bytes")
                    else:
                        # Assume it's already bytes
                        audio_bytes = audio
                        print(f"🔍 Debug: Using audio as bytes: {len(audio_bytes)} bytes")
                    
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
