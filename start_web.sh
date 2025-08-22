#!/bin/bash

# Grok Playground Web Interface Startup Script

echo "🎭 Starting Grok Playground Web Interface..."

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "⚠️ Virtual environment not found. Creating one..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install/update dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

# Load environment variables
echo "🔑 Loading environment variables..."
set -a
[ -f .env ] && . ./.env || true

# Check for required API key
if [ -z "$XAI_API_KEY" ]; then
    echo "⚠️ XAI_API_KEY not found in environment or .env file"
    echo "Please set your xAI API key:"
    echo "export XAI_API_KEY='your_api_key_here'"
    echo "Or add it to your .env file"
    exit 1
fi

# Get local IP for external access
LOCAL_IP=$(ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -1)
if [ -z "$LOCAL_IP" ]; then
    LOCAL_IP="127.0.0.1"
fi

echo ""
echo "🚀 Starting web server..."
echo "📍 Local access: http://localhost:8080"
echo "🌐 Network access: http://$LOCAL_IP:8080"
echo ""
echo "💡 To access from outside your network:"
echo "   1. Configure port forwarding on your router (port 8080)"
echo "   2. Use your external IP: http://$(curl -s ifconfig.me):8080"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Start the Flask application
python web_app.py
