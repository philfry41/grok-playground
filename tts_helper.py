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
            
            # Determine if we should save or play based on auto_save setting
            should_save = save_audio or self.auto_save
            
            if should_save:
                # Create audio directory if it doesn't exist
                audio_dir = "audio"
                os.makedirs(audio_dir, exist_ok=True)
                
                # Save to a timestamped file in audio directory
                import datetime
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"grok_response_{timestamp}.mp3"
                filepath = os.path.join(audio_dir, filename)
                
                with open(filepath, "wb") as f:
                    for chunk in audio:
                        f.write(chunk)
                print(f"💾 Audio saved to: {filepath}")
                return filepath
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
