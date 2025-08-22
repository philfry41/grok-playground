#!/usr/bin/env python3

import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environment variables
if os.path.exists('.env'):
    with open('.env', 'r') as f:
        for line in f:
            if line.strip() and not line.startswith('#'):
                key, value = line.strip().split('=', 1)
                os.environ[key] = value

try:
    print("Testing imports...")
    from grok_remote import chat_with_grok
    print("✅ grok_remote imported successfully")
    
    from tts_helper import tts
    print("✅ tts_helper imported successfully")
    
    from flask import Flask
    print("✅ Flask imported successfully")
    
    print("\nTesting web app creation...")
    app = Flask(__name__)
    print("✅ Flask app created successfully")
    
    print("\nTesting basic route...")
    @app.route('/test')
    def test():
        return "Web app is working!"
    
    print("✅ Route created successfully")
    
    print("\nAll tests passed! The web app should work.")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
