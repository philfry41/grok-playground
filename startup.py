#!/usr/bin/env python3
"""
Startup script for Render deployment
Runs database migrations and starts the web application
"""

import os
import sys
import subprocess

def run_migrations():
    """Run database migrations"""
    try:
        print("🗄️ Running database migrations...")
        
        # Set Flask app environment variable
        os.environ['FLASK_APP'] = 'web_app.py'
        
        # Run migrations
        result = subprocess.run(['flask', 'db', 'upgrade'], 
                              capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0:
            print("✅ Database migrations completed successfully")
            print(f"Migration output: {result.stdout}")
        else:
            print(f"⚠️ Migration warning: {result.stderr}")
            print(f"Migration output: {result.stdout}")
            
    except subprocess.TimeoutExpired:
        print("⏰ Migration timed out, continuing with startup...")
    except Exception as e:
        print(f"⚠️ Migration error (continuing anyway): {e}")

def main():
    """Main startup function"""
    print("🚀 Starting Grok Playground on Render...")
    
    # Run migrations first
    run_migrations()
    
    # Start the web application
    print("🌐 Starting web application...")
    os.system("python web_app.py")

if __name__ == '__main__':
    main()
