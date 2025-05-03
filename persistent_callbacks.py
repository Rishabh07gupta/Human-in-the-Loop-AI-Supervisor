import json
import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Path to store callback registry
CALLBACKS_FILE = "callback_registry.json"

class CallbackRegistry:
    """
    Persistent registry to store request callbacks between agent sessions.
    This ensures requests made in previous sessions can still be resolved.
    """
    def __init__(self):
        self.callbacks_map = {}
        self.load_from_disk()
    
    def register(self, request_id: int, session_id: str):
        """Register a callback for a request"""
        self.callbacks_map[str(request_id)] = session_id
        self.save_to_disk()
        logger.info(f"Registered callback for request {request_id} with session {session_id}")
    
    def get_session_for_request(self, request_id: int) -> str:
        """Get the session ID for a request"""
        return self.callbacks_map.get(str(request_id))
    
    def remove(self, request_id: int):
        """Remove a callback once resolved"""
        if str(request_id) in self.callbacks_map:
            del self.callbacks_map[str(request_id)]
            self.save_to_disk()
            logger.info(f"Removed callback for request {request_id}")
    
    def save_to_disk(self):
        """Persist callbacks to disk"""
        try:
            with open(CALLBACKS_FILE, 'w') as f:
                json.dump(self.callbacks_map, f)
        except Exception as e:
            logger.error(f"Failed to save callbacks to disk: {e}")
    
    def load_from_disk(self):
        """Load callbacks from disk"""
        try:
            if os.path.exists(CALLBACKS_FILE):
                with open(CALLBACKS_FILE, 'r') as f:
                    self.callbacks_map = json.load(f)
                logger.info(f"Loaded {len(self.callbacks_map)} callbacks from disk")
        except Exception as e:
            logger.error(f"Failed to load callbacks from disk: {e}")
            self.callbacks_map = {}

# Singleton instance
callback_registry = CallbackRegistry()