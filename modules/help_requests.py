from datetime import datetime
import logging
from typing import List, Optional, Dict
import requests
import os
from requests.adapters import HTTPAdapter
from urllib3 import Retry
from database import db, HelpRequest
from modules.knowledge_base import add_to_knowledge_base
from flask import current_app 

# Configure logging
logger = logging.getLogger(__name__)

# Memory-based storage
memory_help_requests: Dict[int, object] = {}
next_request_id: int = 1

FLASK_API_URL = os.environ.get("FLASK_API_URL", "http://localhost:5000")

class MockHelpRequest:
    """Simplified mock for HelpRequest when outside Flask context or for memory-only items."""
    def __init__(self, customer_id, question, status='pending', webhook_url=None):
        global next_request_id # Allow modification for memory-only ID assignment
        self.id: Optional[int] = None
        self.customer_id: str = customer_id
        self.question: str = question
        self.status: str = status
        self.answer: Optional[str] = None
        self.created_at: datetime = datetime.utcnow()
        self.resolved_at: Optional[datetime] = None
        self.webhook_url: Optional[str] = webhook_url

        if self.id is None and not _is_flask_context_available_for_db():
            self.id = next_request_id
            next_request_id += 1


def _is_flask_context_available_for_db():
    """Checks if Flask app context is available for database operations."""
    try:
        from flask import current_app, has_app_context
        return has_app_context() and current_app is not None
    except ImportError:
        return False
    except RuntimeError: # Outside of application context
        return False


def create_help_request(customer_id: str, question: str, webhook_url: str):
    """Creates a help request, trying DB first, then memory."""

    if _is_flask_context_available_for_db():
        from flask import current_app
        try:
            with current_app.app_context(): # Ensure operations are within context
                help_request_db = HelpRequest(
                    customer_id=customer_id,
                    question=question,
                    status='pending',
                    webhook_url=webhook_url
                )
                db.session.add(help_request_db)
                db.session.commit()
                logger.info(f"Help request ID {help_request_db.id} created in DB for customer {customer_id}.")
                
                # Update memory store for consistency 
                memory_help_requests[help_request_db.id] = help_request_db
                return help_request_db
        except Exception as e:
            logger.error(f"DB error creating help request for {customer_id}: {e}. Falling back to memory.", exc_info=True)
            if 'db' in locals() and db.session.is_active: # Check if db object exists and session is active
                db.session.rollback()

    # Fallback to memory-only if no context or DB error
    logger.warning(f"Creating help request for customer {customer_id} in memory only.")
    mock_request = MockHelpRequest(customer_id, question, webhook_url=webhook_url)

    if mock_request.id is not None:
        memory_help_requests[mock_request.id] = mock_request
        logger.info(f"Mock help request ID {mock_request.id} created in memory for customer {customer_id}.")
        return mock_request
    else:
        logger.error(f"Failed to create mock help request in memory (ID assignment failed).")
        return None

def resolve_request(request_id: int, answer: str):
    """Resolves a help request, updating DB and then knowledge base."""
    help_request_obj = None  # Initialize

    if _is_flask_context_available_for_db():
        try:
            with current_app.app_context():
                help_request_db = get_help_request(request_id)
                if not help_request_db:
                    logger.warning(f"Request ID {request_id} not found in DB for resolving. Checking memory.")
                    # Fall through to memory check if not in DB
                else:
                    help_request_db.status = 'resolved'
                    help_request_db.answer = answer
                    help_request_db.resolved_at = datetime.utcnow()
                    # The add_to_knowledge_base is called after commit to ensure data is stable
                    db.session.commit()
                    logger.info(f"Request ID {request_id} resolved in DB. Answer: '{answer[:50]}...'")
                    help_request_obj = help_request_db 

                    # Add to knowledge base (this will trigger FAISS index rebuild)
                    add_to_knowledge_base(help_request_db.question, answer)

                    # Send webhook if URL exists
                    if help_request_db.webhook_url:
                        # Append request_id to webhook_url
                        webhook_url = f"{help_request_db.webhook_url.rstrip('/')}/{help_request_db.id}"
                        webhook_payload = {'answer': answer, 'request_id': help_request_db.id}
                        session = requests.Session()
                        retries = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
                        session.mount('http://', HTTPAdapter(max_retries=retries))
                        try:
                            logger.info(f"Sending 'resolved' webhook to {webhook_url} for request {help_request_db.id}")
                            response = session.post(webhook_url, json=webhook_payload, timeout=10)
                            response.raise_for_status()  # Check for HTTP errors
                            logger.info(f"Webhook for request {help_request_db.id} sent successfully.")
                        except requests.exceptions.RequestException as e_req:
                            logger.error(f"Webhook POST failed for request {help_request_db.id} to {webhook_url}: {e_req}")
                    return help_request_obj  # Return the DB object
        except Exception as e:
            logger.error(f"DB error resolving request {request_id}: {e}. Checking memory.", exc_info=True)
            if 'db' in locals() and db.session.is_active:
                db.session.rollback()

    # Memory fallback (if no DB context or DB op failed above)
    if request_id in memory_help_requests:
        mem_request = memory_help_requests[request_id]
        if isinstance(mem_request, MockHelpRequest) or hasattr(mem_request, 'status'):
            mem_request.status = 'resolved'
            mem_request.answer = answer
            if hasattr(mem_request, 'resolved_at'):
                mem_request.resolved_at = datetime.utcnow()
            logger.info(f"Request ID {request_id} (memory) resolved. Answer: '{answer[:50]}...'")
            
            # Still try to update knowledge base
            question_to_add = mem_request.question if hasattr(mem_request, 'question') else "Unknown question from memory"
            add_to_knowledge_base(question_to_add, answer)

            if hasattr(mem_request, 'webhook_url') and mem_request.webhook_url:
                # Append request_id to webhook_url
                webhook_url = f"{mem_request.webhook_url.rstrip('/')}/{mem_request.id}"
                webhook_payload = {'answer': answer, 'request_id': mem_request.id}
                session = requests.Session()
                retries = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
                session.mount('http://', HTTPAdapter(max_retries=retries))
                try:
                    logger.info(f"Sending 'resolved' webhook (from memory path) to {webhook_url} for request {mem_request.id}")
                    response = session.post(webhook_url, json=webhook_payload, timeout=10)
                    response.raise_for_status() 
                    logger.info(f"Webhook for request {mem_request.id} sent successfully.")
                except requests.exceptions.RequestException as e_req:
                    logger.error(f"Webhook POST failed for memory request {mem_request.id} to {webhook_url}: {e_req}")
            return mem_request  # Return the memory object
        else:
            logger.warning(f"Memory object for request ID {request_id} is not a valid request type.")
            return None
    
    logger.error(f"Request ID {request_id} not found for resolving in DB or memory.")
    return None


def get_knowledge_for_question(question: str) -> Optional[object]:
    """Checks knowledge base via API."""
    if not question or not question.strip():
        logger.warning("get_knowledge_for_question called with empty question.")
        return None
    try:
        logger.info(f"Querying knowledge API '{FLASK_API_URL}/api/knowledge/query' for: '{question[:70]}...'")
        response = requests.post(
            f"{FLASK_API_URL}/api/knowledge/query",
            json={'question': question},
            timeout=15 
        )
        response.raise_for_status()
        
        data = response.json()
        if data.get('success') and data.get('found'):
            logger.info(f"Knowledge API found answer for '{question[:70]}...'. Match: {data.get('match_type')}, Score: {data.get('score', 'N/A')}")
            
            class KnowledgeAPIResult:
                def __init__(self, id_val, q_val, a_val, score_val=None, match_type_val=None):
                    self.id = id_val
                    self.question = q_val # Matched question from KB
                    self.answer = a_val
                    self.score = score_val
                    self.match_type = match_type_val
            return KnowledgeAPIResult(
                id_val=data.get('id'),
                q_val=data.get('question'), 
                a_val=data.get('answer'),
                score_val=data.get('score'),
                match_type_val=data.get('match_type')
            )
        else:
            log_message = f"Knowledge API did not find an answer for '{question[:70]}...'."
            if 'message' in data: log_message += f" API Msg: {data['message']}"
            if 'error' in data: log_message += f" API Err: {data['error']}"
            logger.info(log_message)
            return None

    except requests.exceptions.Timeout:
        logger.error(f"Knowledge API query timed out for: '{question[:70]}...'")
        return None
    except requests.exceptions.RequestException as e_req:
        logger.error(f"Knowledge API query failed (RequestException) for '{question[:70]}...': {e_req}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in get_knowledge_for_question for '{question[:70]}...': {e}", exc_info=True)
        return None


def get_help_request(request_id: int):
    """Gets a help request by ID, trying DB then memory."""
    if _is_flask_context_available_for_db():
        from flask import current_app
        try:
            with current_app.app_context():
                return HelpRequest.query.get(request_id)
        except Exception as e:
            logger.warning(f"DB error getting help request {request_id}: {e}. Trying memory.", exc_info=True)
    
    # Memory fallback
    if request_id in memory_help_requests:
        logger.debug(f"Returning help request {request_id} from memory.")
        return memory_help_requests[request_id]
    
    logger.warning(f"Help request {request_id} not found in DB or memory.")
    return None


def get_pending_requests():
    """Gets all pending help requests, trying DB then memory."""
    if _is_flask_context_available_for_db():
        from flask import current_app
        try:
            with current_app.app_context():
                return HelpRequest.query.filter_by(status='pending').order_by(HelpRequest.created_at.asc()).all()
        except Exception as e:
            logger.warning(f"DB error getting pending requests: {e}. Trying memory.", exc_info=True)
            
    # Memory fallback
    logger.info("Returning pending requests from memory (no DB context or DB error).")
    pending_mem = [req for req in memory_help_requests.values() if hasattr(req, 'status') and req.status == 'pending']
    # Sort memory requests by created_at if available
    return sorted(pending_mem, key=lambda r: r.created_at if hasattr(r, 'created_at') else datetime.min)


def mark_request_unresolved(request_id: int):
    """Marks a help request as unresolved, trying DB then memory."""
    updated_request = None
    if _is_flask_context_available_for_db():
        from flask import current_app
        try:
            with current_app.app_context():
                help_request_db = HelpRequest.query.get(request_id)
                if help_request_db:
                    help_request_db.status = 'unresolved'
                    db.session.commit()
                    logger.info(f"Help request {request_id} marked as unresolved in DB.")
                    updated_request = help_request_db
                else:
                    logger.warning(f"Request ID {request_id} not found in DB to mark unresolved.")
        except Exception as e:
            logger.error(f"DB error marking request {request_id} unresolved: {e}. Trying memory.", exc_info=True)
            if 'db' in locals() and db.session.is_active: db.session.rollback()

    # Memory fallback (if no DB context or DB op failed AND request was not updated in DB)
    if not updated_request and request_id in memory_help_requests:
        mem_request = memory_help_requests[request_id]
        if hasattr(mem_request, 'status'):
            mem_request.status = 'unresolved'
            logger.info(f"Help request {request_id} (memory) marked as unresolved.")
            updated_request = mem_request
        else:
            logger.warning(f"Memory object for request ID {request_id} cannot be marked unresolved (no status attr).")
    
    if not updated_request:
         logger.error(f"Help request {request_id} not found to mark as unresolved in DB or memory.")

    return updated_request