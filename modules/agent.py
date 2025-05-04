import asyncio
import json
import logging
from datetime import datetime
import ssl
import os
import uuid
import certifi
import requests
from aiohttp import web

from livekit.agents import Agent, AgentSession, JobContext, RunContext, WorkerOptions, cli, function_tool
from livekit.plugins import deepgram, openai, silero

from modules.help_requests import create_help_request, get_knowledge_for_question, memory_help_requests
from modules.knowledge_base import get_salon_info_standalone, init_sample_salon_data, add_to_knowledge_base
from persistent_callbacks import callback_registry

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FLASK_API_URL = "http://localhost:5000"

# Global dictionary to store active sessions by ID
active_sessions = {}

# Initialize sample knowledge base
def init_sample_knowledge():
    """Initialize sample knowledge base items for testing."""
    sample_knowledge = [
        {
            "question": "How much is a men's haircut?",
            "answer": "Our men's haircuts are $30."
        },
        {
            "question": "Do you take walk-ins?",
            "answer": "Yes, we accept walk-ins based on availability, but appointments are recommended to ensure you can see your preferred stylist."
        },
        {
            "question": "What are your cancellation policies?",
            "answer": "We request at least 24 hours notice for cancellations. Late cancellations may incur a fee of 50% of the service price."
        },
        
    ]
    
    for item in sample_knowledge:
        add_to_knowledge_base(item["question"], item["answer"])
    
    logger.info("Sample knowledge base initialized")

class SalonAgent(Agent):
    def __init__(self):
        salon_info = get_salon_info_standalone()
        logger.info("Retrieved salon info for agent")
        
        instructions = f"""You are Bella, the AI receptionist for Elegant Beauty Salon & Spa. Your role is to provide exceptional customer service while strictly adhering to these protocols:

            # CORE OPERATING FRAMEWORK
            1. INFORMATION ACCURACY
            - Only provide information explicitly contained in the salon details below
            - Never extrapolate, estimate, or guess service details
            - IMPORTANT: For any uncertain information, IMMEDIATELY use the request_help function BEFORE responding to the customer

            2. CONVERSATIONAL PROTOCOLS
            - Maintain a warm, professional tone (friendly but not overly casual)
            - Use natural salon terminology (e.g., "root touch-up" not "color application")
            - Mirror the customer's language style (formal/casual)
            - Anticipate follow-up questions (e.g., mention aftercare when booking color services)

            3. ESCALATION TRIGGERS
            Immediately use request_help WITHOUT saying "Let me check that for you" first for:
            - Any service/pricing not explicitly listed
            - Appointment availability requests
            - Complex service combinations (e.g., "Can I get highlights with a keratin treatment?")
            - Special requests (e.g., disabilities, allergies)
            - Complaints or sensitive situations
            - ANY questions about discounts, promotions, or pricing exceptions
            
            4. CRITICAL PROCEDURE
            - NEVER say "Let me check that for you" without IMMEDIATELY calling the request_help function
            - Always call request_help FIRST, then respond to the customer based on the result
            - Create help requests IMMEDIATELY - don't wait for the customer to follow up

            # SALON KNOWLEDGE BASE
            {self._format_salon_info(salon_info)}

            # ADVANCED HANDLING EXAMPLES
            - When asked about unavailable times: "I don't see that slot available, but let me check alternatives"
            - For product questions: "We carry professional-grade products like [brand]. Would you like specific recommendations?"
            - For service combinations: "Those services can be combined, but I'll verify with our stylists for timing"
            """
        
        super().__init__(instructions=instructions)
        self.session_id = str(uuid.uuid4())
        self.webhook_server = None
        logger.info(f"Created new agent with session ID: {self.session_id}")

    def _format_salon_info(self, info):
        """Structure the salon info for optimal LLM comprehension"""
        return "\n".join([
            "=== SALON DETAILS ===",
            info.replace(":", ":\n- ").replace("\n", "\n- "),
            "====================="
        ])

    async def start_webhook_server(self):
        app = web.Application()
        app.router.add_post('/webhook/resolved/{request_id}', self.handle_resolved_webhook)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, 'localhost', 5001)
        await site.start()
        self.webhook_server = runner
        logger.info("Webhook server started on port 5001")

    def register_session(self, session):
        """Register the agent session for callbacks"""
        active_sessions[self.session_id] = session
        logger.info(f"Registered session {self.session_id}")

    async def handle_resolved_webhook(self, request):
        try:
            request_id = int(request.match_info['request_id'])
            data = await request.json()
            answer = data.get('answer')
            
            if not answer:
                return web.Response(text="Missing answer", status=400)
            
            # Get session ID from persistent registry
            session_id = callback_registry.get_session_for_request(request_id)
            if not session_id:
                logger.warning(f"No session found for request {request_id}")
                return web.Response(text="Request ID not found", status=404)
            
            # Find the session
            session = active_sessions.get(session_id)
            if not session:
                logger.warning(f"Session {session_id} not found for request {request_id}")
                return web.Response(text="Session not found", status=404)
            
            # Generate the reply
            await session.generate_reply(instructions=f"My supervisor says: {answer}")
            
            # Remove from registry after successful handling
            callback_registry.remove(request_id)
            return web.Response(text="OK")
                
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return web.Response(text=str(e), status=500)

    @function_tool
    async def request_help(self, context: RunContext, question: str):
        """
        Immediately create a help request and notify the supervisor.
        This function should be called BEFORE responding to the customer with "Let me check" messages.
        """
        customer_id = None
        if hasattr(context, 'session') and context.session and hasattr(context.session, 'room'):
            customer_id = context.session.room.name
        
        if not customer_id:
            customer_id = f"customer-{int(datetime.now().timestamp())}"
        
        logger.info(f"Creating help request for {customer_id}: {question}")
        
        # First check if we already have this answer in our knowledge base
        knowledge = get_knowledge_for_question(question)
        if knowledge:
            logger.info(f"Found knowledge for question: {question}")
            return f"I found an answer: {knowledge.answer}"
        
        # If not in knowledge base, create a help request
        webhook_url = f"http://localhost:5001/webhook/resolved"
        help_request = create_help_request(customer_id, question, webhook_url)
        
        # Register request in the persistent registry
        callback_registry.register(help_request.id, self.session_id)
        
        try:
            self._sync_request_to_flask(help_request)
            logger.info(f"Successfully created help request ID: {help_request.id}")
        except Exception as e:
            logger.error(f"Sync failed: {e}")
        
        return "I'm checking with my supervisor about this question and will get back to you shortly."

    def _sync_request_to_flask(self, help_request):
        payload = {
            "customer_id": help_request.customer_id,
            "question": help_request.question,
            "webhook_url": help_request.webhook_url,
            "created_at": help_request.created_at.isoformat()
        }
        
        try:
            response = requests.post(f"{FLASK_API_URL}/api/sync-request", 
                                json=payload, timeout=5)
            if response.status_code == 200:
                response_data = response.json()
                new_id = response_data['id']
                
                # Update memory storage
                if help_request.id in memory_help_requests:
                    del memory_help_requests[help_request.id]
                    
                help_request.id = new_id
                memory_help_requests[new_id] = help_request
                
                # Update callback registry with new ID
                callback_registry.remove(help_request.id)
                callback_registry.register(new_id, self.session_id)
                    
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            # Keep using the memory ID if sync fails

async def entrypoint(ctx: JobContext):
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    
    try:
        logger.info("Connecting to LiveKit...")
        await ctx.connect()
        
        # Initialize sample data
        init_sample_salon_data()
        init_sample_knowledge()
        
        salon_agent = SalonAgent()
        await salon_agent.start_webhook_server()
        
        session = AgentSession(
            vad=silero.VAD.load(),
            stt=deepgram.STT(model="nova-3"),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=openai.TTS(voice="alloy"),
        )
        
        # Register the session in the agent and global registry
        salon_agent.register_session(session)
        
        await session.start(agent=salon_agent, room=ctx.room)
        logger.info(f"Session started in room: {ctx.room.name}")
        
        await session.generate_reply(instructions=" Greet the caller warmly. Remember that for any questions about discounts, prices not in your knowledge base, appointment availability, or special services, you should IMMEDIATELY use the request_help function")
        
        while True:
            await asyncio.sleep(10)
            
    except Exception as e:
        logger.error(f"Entrypoint error: {e}")
        raise

def run_agent():
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))

if __name__ == "__main__":
    run_agent()