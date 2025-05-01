import asyncio
import json
import logging
from datetime import datetime
import ssl
import os
import certifi
import requests

from livekit.agents import Agent, AgentSession, JobContext, RunContext, WorkerOptions, cli, function_tool
from livekit.plugins import deepgram, openai, silero

from modules.help_requests import create_help_request, get_knowledge_for_question
from modules.knowledge_base import get_salon_info_standalone

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration for connecting to Flask supervisor
FLASK_API_URL = "http://localhost:5000"  # Adjust as needed

class SalonAgent(Agent):
    def __init__(self):
        # Get salon information - now using the standalone version that doesn't require Flask context
        salon_info = get_salon_info_standalone()
        logger.info("Retrieved salon info for agent")
        
        instructions = (
            "You are an AI receptionist for a salon. Your name is Bella. "
            "Be friendly, professional, and helpful. "
            f"Here's what you know about the salon:\n\n{salon_info}\n\n"
            "If you don't know the answer to something, use the request_help tool "
            "and tell the customer you'll check with your supervisor and get back to them."
        )
        
        super().__init__(instructions=instructions)
    
    @function_tool
    async def request_help(
        self,
        context: RunContext,
        question: str,
    ):
        """
        Used when you don't know the answer to a customer's question.
        
        Args:
            question: The customer's question that you need help with
        """
        # Get a customer ID - we'll use the session ID if available, fallback to a generic ID
        customer_id = None
        
        # Try to get customer ID from context session if available
        if hasattr(context, 'session') and context.session and hasattr(context.session, 'room'):
            customer_id = context.session.room.name
        
        # Fallback to session ID or generate a timestamp-based ID
        if not customer_id:
            if hasattr(context, 'session_id'):
                customer_id = f"session-{context.session_id}"
            else:
                customer_id = f"customer-{int(datetime.now().timestamp())}"
        
        logger.info(f"Customer ID for help request: {customer_id}")
        
        # Check if we already know the answer - using the function that now works without Flask context
        knowledge = get_knowledge_for_question(question)
        if knowledge:
            logger.info(f"Found answer in knowledge base: {knowledge.answer}")
            return f"I found an answer in my knowledge base: {knowledge.answer}"
        
        # Create a help request - using the function that now works without Flask context
        help_request = create_help_request(customer_id, question)
        
        # Log the help request
        logger.info(f"Help request created: ID {help_request.id}, Question: {question}")
        print(f"[Supervisor Text Message] Help needed! Question: {question} from customer {customer_id}")
        
        # Try to sync the request with the Flask supervisor app
        try:
            self._sync_request_to_flask(help_request)
        except Exception as e:
            logger.error(f"Failed to sync help request to Flask supervisor: {e}")
        
        max_checks = 20  # Check up to 20 times (about 1 minute with 3 second intervals)
        checks = 0
        
        while checks < max_checks:
            checks += 1
            await asyncio.sleep(3)  # Check every 3 seconds
            
            answer = await self._check_for_resolved_requests(context, help_request.id)
            if answer:
                # If we got an answer, notify the customer
                return f"My supervisor has responded: {answer}"
            
        return "I'll check with my supervisor and get back to you as soon as possible."
    
    def _sync_request_to_flask(self, help_request):
        """
        Attempt to sync a help request to the Flask supervisor app.
        This ensures the request appears in the web UI.
        """
        try:
            # Send the help request to the Flask app
            endpoint = f"{FLASK_API_URL}/api/sync-request"
            
            payload = {
                "id": help_request.id,
                "customer_id": help_request.customer_id,
                "question": help_request.question,
                "status": help_request.status,
                "created_at": help_request.created_at.isoformat() if hasattr(help_request.created_at, 'isoformat') else str(help_request.created_at)
            }
            
            # Log that we're making this request
            logger.info(f"Syncing request to Flask supervisor: {payload}")
            
            # Actually make the HTTP request to sync with Flask
            response = requests.post(endpoint, json=payload, timeout=5)
            if response.status_code != 200:
                logger.error(f"Failed to sync request: {response.text}")
            else:
                logger.info(f"Successfully synced request to Flask app: {response.json()}")
                
        except Exception as e:
            logger.error(f"Error syncing request to Flask: {e}")
            raise
    
    async def _check_for_resolved_requests(self, context: RunContext, request_id: int):
        """
        Check if a help request has been resolved by the supervisor.
        """
        try:
            endpoint = f"{FLASK_API_URL}/api/check-request/{request_id}"
            response = requests.get(endpoint, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if data['status'] == 'resolved':
                    return data['answer']
            return None
        except Exception as e:
            logger.error(f"Error checking request status: {e}")
            return None


async def entrypoint(ctx: JobContext):
    """Entry point for the LiveKit agent."""
    # Set up SSL certificates properly
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    
    try:
        # Connect with verbose logging
        logger.info("Attempting to connect to LiveKit...")
        await ctx.connect()
        logger.info("Successfully connected to LiveKit")

        # Create and configure the agent session
        logger.info("Setting up agent session...")
        session = AgentSession(
            vad=silero.VAD.load(),
            stt=deepgram.STT(model="nova-3"),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=openai.TTS(voice="alloy"),
        )
        logger.info("Agent session created successfully")

        # Start the agent session
        logger.info("Creating salon agent...")
        salon_agent = SalonAgent()
        logger.info("Starting agent session...")
        await session.start(agent=salon_agent, room=ctx.room)
        logger.info(f"Agent session started in room: {ctx.room.name}")
        
        # Welcome message
        logger.info("Sending welcome message...")
        await session.generate_reply(instructions="Greet the caller warmly and ask how you can help them today.")
        logger.info("Welcome message sent")
        
        # Keep the session alive with periodic health checks
        while True:
            logger.debug("Agent heartbeat")
            await asyncio.sleep(10)  # Check every 10 seconds
            
    except Exception as e:
        logger.error(f"Error in agent entrypoint: {e}")
        raise

def run_agent():
    """Function to run the agent via CLI."""
    # Configure environment variables for SSL
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    
    # Start the agent with more detailed logging
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))


if __name__ == "__main__":
    run_agent()