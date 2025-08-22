# 🚀 Deploy to Render

## Step 1: Create Render Account
1. Go to [render.com](https://render.com)
2. Sign up with GitHub (recommended) or email
3. Verify your email

## Step 2: Connect Your Repository
1. Click "New +" → "Web Service"
2. Connect your GitHub account
3. Select your `grok-playground` repository
4. Choose the repository branch (usually `main` or `master`)

## Step 3: Configure the Service
- **Name**: `grok-playground` (or any name you prefer)
- **Environment**: `Python 3`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn web_app:app`
- **Plan**: Free (to start)

## Step 4: Set Environment Variables
Click "Environment" tab and add these variables:

### Required:
- `XAI_API_KEY` = Your xAI API key
- `ELEVENLABS_API_KEY` = Your ElevenLabs API key

### Optional (have defaults):
- `XAI_MODEL` = `grok-3`
- `ELEVENLABS_VOICE_ID` = `pNInz6obpgDQGcFmaJgB`
- `ELEVENLABS_AUTO_SAVE` = `true`
- `ELEVENLABS_MAX_LENGTH` = `5000`

## Step 5: Deploy
1. Click "Create Web Service"
2. Wait for build to complete (2-5 minutes)
3. Your app will be available at: `https://your-app-name.onrender.com`

## Step 6: Test
1. Visit your deployed URL
2. Test all features:
   - Load Opener
   - Chat with Grok
   - TTS functionality
   - Audio playback

## Troubleshooting
- Check build logs if deployment fails
- Ensure all environment variables are set
- Verify API keys are valid
- Check that `opener.txt` exists in your repository

## Notes
- Free tier has limitations (sleeps after inactivity)
- Audio files are stored temporarily (not persistent)
- Consider upgrading to paid plan for production use
