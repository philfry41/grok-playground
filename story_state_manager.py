import json
import re
from typing import Dict, List, Any
from grok_remote import chat_with_grok

class StoryStateManager:
    def __init__(self):
        self.current_state = {
            "characters": {},
            "location": "unknown",
            "positions": "unknown",
            "physical_contact": "none",
            "mood_atmosphere": "neutral",
            "key_objects": [],
            "story_progress": []
        }
    
    def extract_state_from_messages(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Use AI to intelligently extract story state from conversation messages
        """
        try:
            # Create a focused prompt for state extraction
            recent_messages = messages[-4:] if len(messages) > 4 else messages
            
            # Build context from recent messages
            context = "\n".join([f"{msg['role']}: {msg['content']}" for msg in recent_messages])
            
            extraction_prompt = f"""
You are a story state analyzer. Extract the current story state from this conversation and return ONLY a JSON object.

CONVERSATION CONTEXT:
{context}

EXTRACT AND RETURN THIS JSON STRUCTURE:
{{
    "characters": {{
        "character_name": {{
            "clothing": "current clothing state",
            "position": "current body position",
            "mood": "emotional state"
        }}
    }},
    "location": "current location/setting",
    "positions": "overall body positions of characters",
    "physical_contact": "level of physical contact between characters",
    "mood_atmosphere": "overall mood/atmosphere of the scene",
    "key_objects": ["important objects in the scene"],
    "story_progress": ["key plot points achieved"]
}}

RULES:
- Only include characters that are actively present in the scene
- Be specific about clothing states (e.g., "fully dressed", "shirt removed", "naked")
- Be specific about positions (e.g., "sitting", "standing", "lying down", "kneeling")
- Be specific about physical contact (e.g., "none", "touching", "kissing", "penetration")
- Use "unknown" for any state you cannot determine
- Return ONLY the JSON object, no other text
"""
            
            # Call AI to extract state
            response = chat_with_grok(
                [{"role": "user", "content": extraction_prompt}],
                model="grok-3",
                temperature=0.1,  # Low temperature for consistent extraction
                max_tokens=500,
                hide_thinking=True
            )
            
            # Clean and parse the response
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.endswith("```"):
                response = response[:-3]
            
            # Try to parse JSON with better error handling
            try:
                extracted_state = json.loads(response)
            except json.JSONDecodeError as json_error:
                print(f"🔍 Debug: JSON parsing failed: {json_error}")
                print(f"🔍 Debug: Raw response: {response}")
                
                # Try to extract JSON from the response if it's embedded in text
                import re
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    try:
                        extracted_state = json.loads(json_match.group())
                        print(f"🔍 Debug: Successfully extracted JSON from text")
                    except json.JSONDecodeError:
                        print(f"🔍 Debug: Failed to extract valid JSON from text")
                        return self.current_state
                else:
                    print(f"🔍 Debug: No JSON found in response")
                    return self.current_state
            
            # Validate and merge with current state
            self._merge_state(extracted_state)
            
            print(f"🔍 Debug: AI extracted state: {json.dumps(self.current_state, indent=2)}")
            
            return self.current_state
            
        except Exception as e:
            print(f"🔍 Debug: State extraction failed: {e}")
            # Return current state if extraction fails
            return self.current_state
    
    def _merge_state(self, new_state: Dict[str, Any]):
        """
        Intelligently merge new state with current state
        """
        # Merge characters
        if "characters" in new_state:
            for char_name, char_data in new_state["characters"].items():
                if char_name not in self.current_state["characters"]:
                    self.current_state["characters"][char_name] = {
                        "clothing": "fully dressed",
                        "position": "unknown",
                        "mood": "neutral"
                    }
                
                # Update character data
                for key, value in char_data.items():
                    if value != "unknown":
                        self.current_state["characters"][char_name][key] = value
        
        # Update other state fields
        for key in ["location", "positions", "physical_contact", "mood_atmosphere"]:
            if key in new_state and new_state[key] != "unknown":
                self.current_state[key] = new_state[key]
        
        # Update key objects
        if "key_objects" in new_state:
            self.current_state["key_objects"] = new_state["key_objects"]
        
        # Update story progress
        if "story_progress" in new_state:
            self.current_state["story_progress"] = new_state["story_progress"]
        
        # Save updated state to file
        self._save_state()
    
    def get_state_as_prompt(self) -> str:
        """
        Format current state as a prompt for the main AI
        """
        character_list = []
        for char_name, char_data in self.current_state["characters"].items():
            char_info = f"- {char_name}: {char_data['clothing']}, {char_data['position']}, {char_data['mood']}"
            character_list.append(char_info)
        
        if not character_list:
            character_list = ["- No characters tracked yet"]
        
        state_prompt = f"""
CURRENT SCENE STATE (maintain this continuity):
{chr(10).join(character_list)}
- Location: {self.current_state['location']}
- Positions: {self.current_state['positions']}
- Physical contact: {self.current_state['physical_contact']}
- Mood/Atmosphere: {self.current_state['mood_atmosphere']}
- Key objects: {', '.join(self.current_state['key_objects']) if self.current_state['key_objects'] else 'none'}
- Story progress: {', '.join(self.current_state['story_progress']) if self.current_state['story_progress'] else 'beginning'}

Continue the story while maintaining this physical state. Do not have clothes magically reappear or positions change without explicit action.
"""
        return state_prompt
    
    def reset_state(self):
        """
        Reset state to initial values
        """
        self.current_state = {
            "characters": {},
            "location": "unknown",
            "positions": "unknown",
            "physical_contact": "none",
            "mood_atmosphere": "neutral",
            "key_objects": [],
            "story_progress": []
        }
        self._save_state()
    
    def _save_state(self):
        """
        Save current state to file for persistence
        """
        try:
            with open("scene_state.json", "w") as f:
                json.dump(self.current_state, f, indent=2)
            print(f"🔍 Debug: Scene state saved to file")
        except Exception as e:
            print(f"🔍 Debug: Error saving scene state: {e}")
    
    def _load_state(self):
        """
        Load state from file if it exists
        """
        try:
            if os.path.exists("scene_state.json"):
                with open("scene_state.json", "r") as f:
                    loaded_state = json.load(f)
                    self.current_state = loaded_state
                print(f"🔍 Debug: Scene state loaded from file")
                print(f"🔍 Debug: Loaded characters: {list(self.current_state['characters'].keys())}")
            else:
                print(f"🔍 Debug: No scene state file found, using default state")
        except Exception as e:
            print(f"🔍 Debug: Error loading scene state: {e}")
    
    def get_current_state(self):
        """
        Get current state (load from file if needed)
        """
        if not self.current_state.get("characters"):
            self._load_state()
        return self.current_state
