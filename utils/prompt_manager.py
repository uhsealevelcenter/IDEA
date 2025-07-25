import json
import os
from pathlib import Path
from typing import Dict, List, Optional
import logging
from datetime import datetime
import redis

logger = logging.getLogger(__name__)

class PromptManager:
    """Manages CRUD operations for system prompts with file-based persistence and session-specific active prompts"""
    
    def __init__(self, prompts_dir: Optional[Path] = None):
        if prompts_dir is None:
            # Use environment variable or default fallback
            prompt_directory = os.getenv("PROMPT_DIRECTORY", "/app/data/prompts")
            self.prompts_dir = Path(prompt_directory)
        else:
            self.prompts_dir = Path(prompts_dir)
        
        self.prompts_file = self.prompts_dir / "prompts.json"
        self.active_prompt_file = self.prompts_dir / "active_prompt.json"  # Global fallback
        
        # Redis for session-specific active prompts
        try:
            self.redis_client = redis.Redis(host="redis", port=6379, db=0, decode_responses=True)
            self.redis_available = True
        except:
            logger.warning("Redis not available, falling back to global active prompt")
            self.redis_available = False
        
        # Create prompts directory if it doesn't exist
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize with default prompts if file doesn't exist
        if not self.prompts_file.exists():
            self._initialize_default_prompts()
    
    def _initialize_default_prompts(self):
        """Initialize with a simple default prompt"""
        default_prompts = {
            "default": {
                "id": "default",
                "name": "Default Assistant",
                "description": "A helpful AI assistant",
                "content": "You are a helpful AI assistant. Please assist the user with their questions and tasks to the best of your ability.",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
        }
        
        # Save default prompts
        self._save_prompts(default_prompts)
        
        # Set default as the global active prompt
        self._set_global_active_prompt("default")
    

    
    def _save_prompts(self, prompts: Dict):
        """Save prompts to file"""
        with open(self.prompts_file, 'w', encoding='utf-8') as f:
            json.dump(prompts, f, indent=2, ensure_ascii=False)
    
    def _load_prompts(self) -> Dict:
        """Load prompts from file"""
        if not self.prompts_file.exists():
            return {}
        
        try:
            with open(self.prompts_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading prompts: {e}")
            return {}
    
    def _set_global_active_prompt(self, prompt_id: str):
        """Set the global active prompt ID (fallback)"""
        active_data = {"active_prompt_id": prompt_id, "updated_at": datetime.now().isoformat()}
        with open(self.active_prompt_file, 'w', encoding='utf-8') as f:
            json.dump(active_data, f, indent=2)
    
    def _get_global_active_prompt_id(self) -> Optional[str]:
        """Get the global active prompt ID (fallback)"""
        if not self.active_prompt_file.exists():
            return None
        
        try:
            with open(self.active_prompt_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("active_prompt_id")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading active prompt: {e}")
            return None
    
    def _set_session_active_prompt(self, prompt_id: str, session_id: str):
        """Set the active prompt for a specific session"""
        if self.redis_available and session_id:
            try:
                self.redis_client.set(f"active_prompt:{session_id}", prompt_id)
                logger.info(f"Set active prompt {prompt_id} for session {session_id}")
            except Exception as e:
                logger.error(f"Error setting session active prompt: {e}")
        else:
            # Fallback to global if no session or redis unavailable
            self._set_global_active_prompt(prompt_id)
    
    def _get_session_active_prompt_id(self, session_id: Optional[str]) -> Optional[str]:
        """Get the active prompt for a specific session"""
        if self.redis_available and session_id:
            try:
                result = self.redis_client.get(f"active_prompt:{session_id}")
                if result:
                    return result
            except Exception as e:
                logger.error(f"Error getting session active prompt: {e}")
        
        # Fallback to global active prompt
        return self._get_global_active_prompt_id()
    
    def get_active_prompt(self, session_id: Optional[str] = None) -> str:
        """Get the content of the active prompt for the session"""
        active_id = self._get_session_active_prompt_id(session_id)
        if not active_id:
            # Fallback to first available prompt
            prompts = self._load_prompts()
            if prompts:
                active_id = list(prompts.keys())[0]
                if session_id:
                    self._set_session_active_prompt(active_id, session_id)
                else:
                    self._set_global_active_prompt(active_id)
            else:
                return ""
        
        prompts = self._load_prompts()
        return prompts.get(active_id, {}).get("content", "")
    
    def list_prompts(self, session_id: Optional[str] = None) -> List[Dict]:
        """List all prompts with metadata, marking session-specific active prompt"""
        prompts = self._load_prompts()
        active_id = self._get_session_active_prompt_id(session_id)
        
        prompt_list = []
        for prompt_id, prompt_data in prompts.items():
            prompt_list.append({
                "id": prompt_id,
                "name": prompt_data.get("name", ""),
                "description": prompt_data.get("description", ""),
                "content": prompt_data.get("content", ""),
                "created_at": prompt_data.get("created_at", ""),
                "updated_at": prompt_data.get("updated_at", ""),
                "is_active": prompt_id == active_id
            })
        
        return prompt_list
    
    def get_prompt(self, prompt_id: str, session_id: Optional[str] = None) -> Optional[Dict]:
        """Get a specific prompt by ID with session-specific active status"""
        prompts = self._load_prompts()
        active_id = self._get_session_active_prompt_id(session_id)
        
        if prompt_id not in prompts:
            return None
        
        prompt_data = prompts[prompt_id].copy()
        prompt_data["is_active"] = prompt_id == active_id
        return prompt_data
    
    def create_prompt(self, name: str, description: str, content: str) -> Dict:
        """Create a new prompt"""
        prompts = self._load_prompts()
        
        # Generate a unique ID
        prompt_id = name.lower().replace(" ", "_").replace("-", "_")
        counter = 1
        original_id = prompt_id
        while prompt_id in prompts:
            prompt_id = f"{original_id}_{counter}"
            counter += 1
        
        # Create new prompt
        new_prompt = {
            "id": prompt_id,
            "name": name,
            "description": description,
            "content": content,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        # Add to prompts
        prompts[prompt_id] = new_prompt
        self._save_prompts(prompts)
        
        # For consistency, set is_active to False for new prompts
        new_prompt["is_active"] = False
        
        logger.info(f"Created new prompt: {prompt_id}")
        return new_prompt
    
    def update_prompt(self, prompt_id: str, name: Optional[str] = None, 
                     description: Optional[str] = None, content: Optional[str] = None) -> Optional[Dict]:
        """Update an existing prompt"""
        prompts = self._load_prompts()
        
        if prompt_id not in prompts:
            return None
        
        # Update fields if provided
        if name is not None:
            prompts[prompt_id]["name"] = name
        if description is not None:
            prompts[prompt_id]["description"] = description
        if content is not None:
            prompts[prompt_id]["content"] = content
        
        prompts[prompt_id]["updated_at"] = datetime.now().isoformat()
        
        # Save updated prompts
        self._save_prompts(prompts)
        
        # Add is_active field for API response (set to False for consistency)
        updated_prompt = prompts[prompt_id].copy()
        updated_prompt["is_active"] = False
        
        logger.info(f"Updated prompt: {prompt_id}")
        return updated_prompt
    
    def delete_prompt(self, prompt_id: str) -> bool:
        """Delete a prompt"""
        prompts = self._load_prompts()
        
        if prompt_id not in prompts:
            return False
        
        # Check if this is the global active prompt
        global_active_id = self._get_global_active_prompt_id()
        if prompt_id == global_active_id:
            # Set a different prompt as active (first available)
            remaining_prompts = {k: v for k, v in prompts.items() if k != prompt_id}
            if remaining_prompts:
                new_active = list(remaining_prompts.keys())[0]
                self._set_global_active_prompt(new_active)
            else:
                # No prompts left, create a default one
                self._initialize_default_prompts()
                return True
        
        # Remove the prompt
        del prompts[prompt_id]
        self._save_prompts(prompts)
        
        logger.info(f"Deleted prompt: {prompt_id}")
        return True
    
    def set_active_prompt(self, prompt_id: str, session_id: Optional[str] = None) -> bool:
        """Set a prompt as the active one for the session"""
        prompts = self._load_prompts()
        
        if prompt_id not in prompts:
            return False
        
        if session_id:
            self._set_session_active_prompt(prompt_id, session_id)
            logger.info(f"Set active prompt {prompt_id} for session {session_id}")
        else:
            self._set_global_active_prompt(prompt_id)
            logger.info(f"Set global active prompt: {prompt_id}")
        
        return True

# Global instance (will be initialized in app.py)
prompt_manager: Optional[PromptManager] = None

def init_prompt_manager(prompts_dir: Optional[Path] = None):
    """Initialize the global prompt manager instance"""
    global prompt_manager
    prompt_manager = PromptManager(prompts_dir)

def get_prompt_manager() -> PromptManager:
    """Get the global prompt manager instance"""
    if prompt_manager is None:
        raise RuntimeError("PromptManager not initialized. Call init_prompt_manager() first.")
    return prompt_manager 