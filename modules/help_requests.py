from datetime import datetime
import logging
from typing import List, Optional, Dict
import requests
from database import db, HelpRequest

# Configure logging
logger = logging.getLogger(__name__)

# Memory-based storage for when outside of Flask context
memory_help_requests = {}
next_request_id = 1

# Flask API URL
FLASK_API_URL = "http://localhost:5000"

class MockHelpRequest:
    """Mock HelpRequest class for use outside of Flask context"""
    def __init__(self, customer_id, question, status='pending'):
        self.id = None  # Will be set after syncing
        self.customer_id = customer_id
        self.question = question
        self.status = status
        self.answer = None
        self.created_at = datetime.utcnow()
        self.resolved_at = None
        self.webhook_url = None

def is_flask_context_available():
    """Check if we're in a Flask application context"""
    try:
        from flask import has_app_context
        return has_app_context()
    except Exception:
        return False

def create_help_request(customer_id: str, question: str, webhook_url: str):
    try:
        # Existing database code
        from database import db, HelpRequest
        help_request = HelpRequest(
            customer_id=customer_id,
            question=question,
            status='pending',
            webhook_url=webhook_url
        )
        db.session.add(help_request)
        db.session.commit()
        memory_help_requests[help_request.id] = help_request
        return help_request
    except Exception as e:
        # Create mock request without ID first
        help_request = MockHelpRequest(
            customer_id=customer_id,
            question=question
        )
        help_request.webhook_url = webhook_url
        
        # Assign temporary memory ID
        global next_request_id
        help_request.id = next_request_id
        memory_help_requests[next_request_id] = help_request
        next_request_id += 1
        
        return help_request

def resolve_request(request_id: int, answer: str):
    help_request = get_help_request(request_id)
    if not help_request:
        return None
    
    try:
        from database import db
        help_request.status = 'resolved'
        help_request.answer = answer
        help_request.resolved_at = datetime.utcnow()  # Make sure to set resolved_at
        db.session.commit()
        
        # Add to knowledge base
        from modules.knowledge_base import add_to_knowledge_base
        add_to_knowledge_base(help_request.question, answer)
        
        if help_request.webhook_url:
            requests.post(
                f"{help_request.webhook_url}/{help_request.id}",
                json={'answer': answer},
                timeout=5
            )
    except Exception as e:
        help_request.status = 'resolved'
        help_request.answer = answer
        # Still try to add to knowledge base even if DB fails   
        from modules.knowledge_base import add_to_knowledge_base
        add_to_knowledge_base(help_request.question, answer)
    
    print(f"[Customer SMS] Answer: {answer}")
    return help_request

def get_knowledge_for_question(question: str):
    """
    Check if we already have knowledge for a similar question.
    First tries the Flask API endpoint, falls back to in-memory check.
    """
    try:
        # Try to use the Flask API to query the knowledge base
        response = requests.post(
            f"{FLASK_API_URL}/api/knowledge/query",
            json={'question': question},
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success') and data.get('found'):
                # Create a knowledge item object from the API response
                class KnowledgeResult:
                    def __init__(self, id, question, answer):
                        self.id = id
                        self.question = question
                        self.answer = answer
                
                return KnowledgeResult(
                    id=data.get('id'),
                    question=question,
                    answer=data.get('answer')
                )
        
        # If API call fails or doesn't find anything, continue to other methods
        
    except Exception as e:
        logger.warning(f"API knowledge query failed: {e}")
    
    try:
        # Try to use Flask-SQLAlchemy directly
        from database import KnowledgeItem
        
        if not is_flask_context_available():
            raise ValueError("No Flask app context")
            
        # Simple exact match for now
        return KnowledgeItem.query.filter_by(question=question).first()
    
    except Exception as e:
        # If Flask context is not available, check in-memory knowledge base
        logger.warning(f"Could not query knowledge base: {e}")
        from modules.knowledge_base import memory_knowledge_items
        
        # Simple exact match in memory storage
        for item in memory_knowledge_items.values():
            if item.question == question:
                return item
                
        return None


def get_help_request(request_id: int):
    """Get a help request by ID."""
    try:
        from database import HelpRequest
        
        if not is_flask_context_available():
            raise ValueError("No Flask app context")
            
        return HelpRequest.query.get(request_id)
    except Exception as e:
        logger.warning(f"Could not query database for help request: {e}")
        return memory_help_requests.get(request_id)


def get_pending_requests() -> List:
    """Get all pending help requests."""
    try:
        from database import HelpRequest
        
        if not is_flask_context_available():
            raise ValueError("No Flask app context")
            
        return HelpRequest.query.filter_by(status='pending').order_by(HelpRequest.created_at).all()
    except Exception as e:
        logger.warning(f"Could not query database for pending requests: {e}")
        return [req for req in memory_help_requests.values() if req.status == 'pending']
    


def mark_request_unresolved(request_id: int):
    """Mark a help request as unresolved."""
    help_request = get_help_request(request_id)
    if not help_request:
        logger.error(f"Help request {request_id} not found")
        return None
    
    try:
        # Try to use database
        from database import db
        
        if not is_flask_context_available():
            raise ValueError("No Flask app context")
            
        help_request.status = 'unresolved'
        
        db.session.commit()
    
    except Exception as e:
        # If Flask context is not available, update in-memory
        logger.warning(f"Could not update help request in database: {e}. Using in-memory storage.")
        help_request.status = 'unresolved'
    
    logger.info(f"Help request {request_id} marked as unresolved")
    return help_request