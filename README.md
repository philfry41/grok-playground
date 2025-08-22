# Grok Playground (NSFW Storytelling)

Local tester that calls xAI's Grok-4 API for explicit, slow-burn storytelling with edging controls.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export XAI_API_KEY="your_real_xai_key"
export XAI_MODEL="grok-3"  # optional; defaults to grok-3
export ELEVENLABS_API_KEY="your_elevenlabs_key"  # optional; enables TTS
export ELEVENLABS_VOICE_ID="pNInz6obpgDQGcFmaJgB"  # optional; Adam voice
export ELEVENLABS_AUTO_SAVE="true"  # optional; true=save files, false=play audio
export ELEVENLABS_MAX_LENGTH="5000"  # optional; max chars for TTS (0=no limit)
```

Or run the helper:

```bash
./start_grok.sh
```

**Note**: Audio files are automatically saved to the `audio/` directory when TTS is enabled.

Notes:
- You can create a `.env` file with `XAI_API_KEY` (and optionally `XAI_MODEL`), or let `start_grok.sh` prompt and save it.
- If you see a 400 error, the app now prints the server's error JSON to help diagnose (e.g., invalid model name or parameters).

## Commands

| Command | Description |
|---------|-------------|
| `exit` | Quit the application |
| `/new` | Reset to initial priming messages (keeps system prompts) |
| `/raw` | Reassert explicit tone - inserts system message to use blunt anatomical language |
| `/edge` | Enable edging mode: Stephanie can climax, Dan cannot (default state) |
| `/payoff` | Allow both characters to climax naturally |
| `/cont [words]` | Continue scene with target ~N words (default 500, range 250-1500). Adjusts token limits but actual length may vary. |
| `/loadopener [file]` | Load prompt text from file (defaults to `opener.txt`) |
| `/tts` | Show TTS status and current voice |
| `/voice [id]` | List available voices or set voice ID |
| `/save` | Save last response as audio file |
| `/ttsmode` | Toggle between auto-save and auto-play modes |
| `/edgelog` | View recent edge trigger logs for refining detection patterns |

## Features

- **Edging Control**: Automatically detects and prevents male climax during edging mode using regex pattern matching
- **Content Filtering**: Soft-enforcement that trims responses and requests redirection if male climax slips through
- **Trigger Logging**: Logs all edge triggers to `edge_triggers.log` for analysis and pattern refinement
- **Model Configuration**: Supports any xAI model via `XAI_MODEL` environment variable
- **Error Handling**: Detailed server error messages and automatic retry with minimal parameters on 400 errors
- **Debug Mode**: Set `XAI_DEBUG=1` to print API request details and server responses
- **Text-to-Speech**: ElevenLabs integration for audio playback or file saving of responses (saved to `audio/` directory)
# Force new deployment
