import os
import tempfile
import subprocess
from elevenlabs import ElevenLabs

class TTSHelper:
    def __init__(self):
        self.api_key = os.getenv("ELEVENLABS_API_KEY")
        self.enabled = bool(self.api_key)
        self.voice_id = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")  # Adam voice
        self.volume = float(os.getenv("ELEVENLABS_VOLUME", "0.5"))
        self.auto_save = os.getenv("ELEVENLABS_AUTO_SAVE", "true").lower() == "true"
        self.max_tts_length = int(os.getenv("ELEVENLABS_MAX_LENGTH", "5000"))  # 0 = no limit
        
        if self.enabled:
            self.client = ElevenLabs(api_key=self.api_key)
            mode = "auto-save" if self.auto_save else "auto-play"
            print(f"🎤 TTS enabled with voice ID: {self.voice_id} (mode: {mode})")
        else:
            print("🔇 TTS disabled - set ELEVENLABS_API_KEY to enable")
    
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
