#!/usr/bin/env python3
"""
Script to upload local story files to the database
"""

import os
import json
import sys
from datetime import datetime

# Add the current directory to Python path so we can import from web_app
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from web_app import app, db, Story, User, DATABASE_AVAILABLE
    print("✅ Successfully imported database models")
except ImportError as e:
    print(f"❌ Failed to import database models: {e}")
    sys.exit(1)

def upload_story_to_database(story_filename, user_google_id="109988614139550624643"):
    """Upload a local story file to the database"""
    
    if not DATABASE_AVAILABLE:
        print("❌ Database not available")
        return False
    
    if not os.path.exists(story_filename):
        print(f"❌ Story file not found: {story_filename}")
        return False
    
    try:
        # Read the story file
        with open(story_filename, 'r', encoding='utf-8') as f:
            story_data = json.load(f)
        
        print(f"📖 Read story: {story_data.get('title', 'Unknown')}")
        print(f"📝 Story ID: {story_data.get('story_id', 'Unknown')}")
        
        with app.app_context():
            # Force recreate tables with correct schema
            print("🔄 Recreating local database tables with correct schema...")
            db.drop_all()
            db.create_all()
            print("✅ Local database tables recreated")
            
            # Check if story already exists
            story_id = story_data.get('story_id', 'unknown')
            existing_story = Story.query.filter_by(story_id=story_id, user_id=user_google_id).first()
            
            if existing_story:
                print(f"⚠️ Story {story_id} already exists for user {user_google_id}")
                print("Updating existing story...")
                existing_story.content = story_data
                existing_story.title = story_data.get('title', story_id)
                existing_story.updated_at = datetime.utcnow()
                db.session.commit()
                print(f"✅ Updated story: {story_id}")
            else:
                # Create new story
                new_story = Story(
                    story_id=story_id,
                    title=story_data.get('title', story_id),
                    user_id=user_google_id,
                    content=story_data,
                    is_public=False,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                
                db.session.add(new_story)
                db.session.commit()
                print(f"✅ Created new story: {story_id}")
            
            return True
            
    except Exception as e:
        print(f"❌ Error uploading story: {e}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return False

def main():
    """Main function"""
    print("🚀 Story Upload Script")
    print("=" * 50)
    
    # Upload the farm romance story
    story_file = "story_farm_romance.json"
    user_id = "109988614139550624643"  # Your Google ID from the logs
    
    print(f"📁 Uploading: {story_file}")
    print(f"👤 User ID: {user_id}")
    print()
    
    success = upload_story_to_database(story_file, user_id)
    
    if success:
        print()
        print("🎉 Story upload completed successfully!")
        print("You can now test /loadstory Farm_romance on Render")
    else:
        print()
        print("❌ Story upload failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
