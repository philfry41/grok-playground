import json
import os
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
            "story_progress": [],
            "arousal_levels": {},
            "clothing_removed": [],
            "body_positions": {}
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
You are a detailed story state analyzer for erotic fiction. Extract the current story state from this conversation and return ONLY a JSON object.

CONVERSATION CONTEXT:
{context}

EXTRACT AND RETURN THIS JSON STRUCTURE:
{{
    "characters": {{
        "character_name": {{
            "clothing": "detailed clothing state (what's on/off, partially removed, etc.)",
            "position": "specific body position and orientation",
            "mood": "emotional/arousal state",
            "physical_state": "body condition (sweating, trembling, etc.)",
            "body_parts_exposed": ["specific body parts that are visible/touched"],
            "interactions": "what they're doing with their hands/body"
        }}
    }},
    "location": "current location/setting with specific details",
    "positions": "detailed body positions and spatial relationships",
    "physical_contact": "specific level and type of physical contact",
    "mood_atmosphere": "overall mood/atmosphere with sexual tension level",
    "key_objects": ["important objects in the scene and their state"],
    "story_progress": ["key plot points and sexual milestones achieved"],
    "arousal_levels": {{
        "character_name": "arousal level (low/medium/high/peak)"
    }},
    "clothing_removed": ["specific items of clothing that have been removed"],
    "body_positions": {{
        "character_name": "detailed body position and what they're doing"
    }}
}}

RULES:
- Only include characters that are actively present in the scene
- Be VERY specific about clothing states (e.g., "shirt unbuttoned, bra visible", "pants around ankles", "completely naked")
- Be VERY specific about positions (e.g., "sitting on edge of bed, legs spread", "kneeling between legs", "lying on back, arms above head")
- Track specific body parts that are exposed, touched, or involved in actions
- Be specific about physical contact (e.g., "fingering pussy", "sucking cock", "grinding against thigh")
- Track arousal levels for each character
- Note any clothing items that have been removed and where they are
- Use "unknown" for any state you cannot determine
- Return ONLY the JSON object, no other text
"""
            
            # Prepare payload for state extraction
            extraction_payload = [{"role": "user", "content": extraction_prompt}]
            
            # Call AI to extract state
            ai_response = chat_with_grok(
                extraction_payload,
                model="grok-3",
                temperature=0.1,  # Low temperature for consistent extraction
                max_tokens=800,  # Increased to prevent JSON truncation
                hide_thinking=True,
                return_usage=True
            )
            
            # Extract response text and usage info
            if isinstance(ai_response, dict):
                response = ai_response['text']
                usage = ai_response['usage']
                finish_reason = ai_response['finish_reason']
            else:
                response = ai_response
                usage = {}
                finish_reason = 'unknown'
            
            # Store payload for debugging
            try:
                from web_app import store_ai_payload
                store_ai_payload('state_extraction', extraction_payload, response, usage, finish_reason)
            except:
                pass  # Ignore if web_app not available
            
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
        # Merge characters with enhanced fields
        if "characters" in new_state:
            for char_name, char_data in new_state["characters"].items():
                if char_name not in self.current_state["characters"]:
                    self.current_state["characters"][char_name] = {
                        "clothing": "fully dressed",
                        "position": "unknown",
                        "mood": "neutral",
                        "physical_state": "normal",
                        "body_parts_exposed": [],
                        "interactions": "none"
                    }
                
                # Update character data
                for key, value in char_data.items():
                    if value != "unknown" and value != []:
                        self.current_state["characters"][char_name][key] = value
        
        # Update other state fields
        for key in ["location", "positions", "physical_contact", "mood_atmosphere"]:
            if key in new_state and new_state[key] != "unknown":
                self.current_state[key] = new_state[key]
        
        # Update enhanced fields
        for key in ["key_objects", "story_progress", "clothing_removed"]:
            if key in new_state:
                self.current_state[key] = new_state[key]
        
        # Update arousal levels
        if "arousal_levels" in new_state:
            if "arousal_levels" not in self.current_state:
                self.current_state["arousal_levels"] = {}
            self.current_state["arousal_levels"].update(new_state["arousal_levels"])
        
        # Update body positions
        if "body_positions" in new_state:
            if "body_positions" not in self.current_state:
                self.current_state["body_positions"] = {}
            self.current_state["body_positions"].update(new_state["body_positions"])
        
        # Save updated state to file
        self._save_state()
    
    def get_state_as_prompt(self) -> str:
        """
        Format current state as a prompt for the main AI
        """
        character_list = []
        for char_name, char_data in self.current_state["characters"].items():
            # Build detailed character info
            char_parts = [char_data.get('clothing', 'unknown clothing')]
            char_parts.append(char_data.get('position', 'unknown position'))
            char_parts.append(char_data.get('mood', 'unknown mood'))
            
            # Add physical state if available
            if char_data.get('physical_state') and char_data['physical_state'] != 'normal':
                char_parts.append(f"({char_data['physical_state']})")
            
            # Add exposed body parts if any
            if char_data.get('body_parts_exposed'):
                char_parts.append(f"exposed: {', '.join(char_data['body_parts_exposed'])}")
            
            # Add interactions if any
            if char_data.get('interactions') and char_data['interactions'] != 'none':
                char_parts.append(f"doing: {char_data['interactions']}")
            
            char_info = f"- {char_name}: {', '.join(char_parts)}"
            character_list.append(char_info)
        
        if not character_list:
            character_list = ["- No characters tracked yet"]
        
        # Build arousal levels info
        arousal_info = ""
        if self.current_state.get('arousal_levels'):
            arousal_parts = []
            for char_name, level in self.current_state['arousal_levels'].items():
                arousal_parts.append(f"{char_name}: {level}")
            arousal_info = f"- Arousal levels: {', '.join(arousal_parts)}\n"
        
        # Build clothing removed info
        clothing_info = ""
        if self.current_state.get('clothing_removed'):
            clothing_info = f"- Clothing removed: {', '.join(self.current_state['clothing_removed'])}\n"
        
        state_prompt = f"""
CRITICAL: MAINTAIN ACCURATE PHYSICAL CONTINUITY - TRACK CHANGES PROPERLY

CURRENT SCENE STATE (TRACK CHANGES ACCURATELY):
{chr(10).join(character_list)}
- Location: {self.current_state['location']}
- Positions: {self.current_state['positions']}
- Physical contact: {self.current_state['physical_contact']}
- Mood/Atmosphere: {self.current_state['mood_atmosphere']}
{arousal_info}{clothing_info}- Key objects: {', '.join(self.current_state['key_objects']) if self.current_state['key_objects'] else 'none'}
- Story progress: {', '.join(self.current_state['story_progress']) if self.current_state['story_progress'] else 'beginning'}

MANDATORY CONTINUITY RULES:
1. CLOTHING: If clothing is removed/partially removed, it STAYS that way until explicitly put back on
2. POSITIONS: Characters can move naturally, but describe the movement when it happens
3. BODY PARTS: Exposed body parts remain exposed until explicitly covered
4. PHYSICAL STATE: Current physical conditions (sweating, trembling, etc.) continue unless explicitly changed
5. OBJECTS: Items remain where they are unless explicitly moved
6. NO MAGICAL RESETS: Do not have clothes magically reappear, positions reset, or body parts become covered without explicit action
7. FOLLOW USER INSTRUCTIONS: When user explicitly requests physical changes (removing clothes, changing positions), follow those instructions and update state tracking accordingly

VIOLATION EXAMPLES TO AVOID:
- Character's shirt is off → next response has them "unbuttoning their shirt" (WRONG)
- Character is naked → next response mentions "removing their dress" (WRONG)  
- Character is sitting → next response has them "standing up" without describing how they moved (WRONG - should describe the movement)
- Character's pants are around ankles → next response has them "pulling down their pants" (WRONG)

Continue the story while maintaining accurate physical state tracking. Follow user instructions for changes and describe all physical changes as explicit actions.
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
            "story_progress": [],
            "arousal_levels": {},
            "clothing_removed": [],
            "body_positions": {}
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
