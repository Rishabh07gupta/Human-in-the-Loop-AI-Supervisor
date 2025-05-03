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
        # Add more common Q&A pairs as needed
    ]
    
    for item in sample_knowledge:
        add_to_knowledge_base(item["question"], item["answer"])
    
    logger.info("Sample knowledge base initialized")

class SalonAgent(Agent):
    def __init__(self):
        salon_info = get_salon_info_standalone()
        logger.info("Retrieved salon info for agent")
        
        instructions = (
            "You are Bella, an AI receptionist for Elegant Beauty Salon. Be friendly, professional, and helpful while maintaining the following guidelines:\n\n"
            
            "# CORE PRINCIPLES:\n"
            "1. ONLY provide information that you're 100% certain about from the salon information below.\n"
            "2. When asked about ANY specific service or pricing NOT explicitly listed in your knowledge, use the request_help tool immediately.\n"
            "3. NEVER make assumptions about services, prices, availability, or stylist information.\n"
            "4. Do not generalize pricing from one service to another (e.g., don't apply men's haircut pricing to women's haircuts).\n\n"
            
            "# WHEN TO USE THE request_help TOOL:\n"
            "- When asked about ANY specific service details not explicitly mentioned below\n"
            "- When asked about pricing for ANY service not explicitly listed with a price\n"
            "- When asked about stylist availability or specific stylist information\n"
            "- When asked about appointment availability for specific dates/times\n"
            "- When asked about product recommendations or availability\n"
            "- When asked policy questions not covered in your information\n\n"
            
            f"# SALON INFORMATION:\n{salon_info}\n\n"
            
            "# EXAMPLE INTERACTIONS:\n\n"
            "## Example 1 - INCORRECT approach:\n"
            "Customer: \"How much is a women's haircut?\"\n"
            "Bella: \"Our haircuts start at $30.\" (DON'T do this if you don't have explicit pricing for women's haircuts)\n\n"
            
            "## Example 1 - CORRECT approach:\n"
            "Customer: \"How much is a women's haircut?\"\n"
            "Bella: \"I don't have the exact pricing for women's haircuts. Let me check with my supervisor for you. One moment please.\" (Then use request_help tool)\n\n"
            
            "## Example 2 - CORRECT approach:\n"
            "Customer: \"What are your hours?\"\n"
            "Bella: \"We're open Monday through Friday from 9:00 AM to 7:00 PM, Saturday from 10:00 AM to 5:00 PM, and we're closed on Sundays.\"\n\n"
            
            "Remember: It's better to use the request_help tool than to provide potentially incorrect information."
        )
        
        super().__init__(instructions=instructions)
        self.session_id = str(uuid.uuid4())
        self.webhook_server = None
        logger.info(f"Created new agent with session ID: {self.session_id}")

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
        customer_id = None
        if hasattr(context, 'session') and context.session and hasattr(context.session, 'room'):
            customer_id = context.session.room.name
        
        if not customer_id:
            customer_id = f"customer-{int(datetime.now().timestamp())}"
        
        logger.info(f"Customer ID: {customer_id}")
        
        knowledge = get_knowledge_for_question(question)
        if knowledge:
            return f"I found an answer: {knowledge.answer}"
        
        webhook_url = f"http://localhost:5001/webhook/resolved"
        help_request = create_help_request(customer_id, question, webhook_url)
        
        # Register request in the persistent registry
        callback_registry.register(help_request.id, self.session_id)
        
        try:
            self._sync_request_to_flask(help_request)
        except Exception as e:
            logger.error(f"Sync failed: {e}")
        
        return "I'll check with my supervisor and get back to you shortly."

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
        
        await session.generate_reply(instructions="Greet the caller warmly.")
        
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