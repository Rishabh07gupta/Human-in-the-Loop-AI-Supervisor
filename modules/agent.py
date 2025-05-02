import asyncio
import json
import logging
from datetime import datetime
import ssl
import os
import certifi
import requests
from aiohttp import web

from livekit.agents import Agent, AgentSession, JobContext, RunContext, WorkerOptions, cli, function_tool
from livekit.plugins import deepgram, openai, silero

from modules.help_requests import create_help_request, get_knowledge_for_question
from modules.knowledge_base import get_salon_info_standalone

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FLASK_API_URL = "http://localhost:5000"

class SalonAgent(Agent):
    def __init__(self):
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
        self.request_callbacks = {}
        self.webhook_server = None

    async def start_webhook_server(self):
        app = web.Application()
        app.router.add_post('/webhook/resolved/{request_id}', self.handle_resolved_webhook)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, 'localhost', 5001)
        await site.start()
        self.webhook_server = runner
        logger.info("Webhook server started on port 5001")

    async def handle_resolved_webhook(self, request):
        try:
            request_id = int(request.match_info['request_id'])
            data = await request.json()
            answer = data.get('answer')
            if not answer:
                return web.Response(text="Missing answer", status=400)
            
            if request_id in self.request_callbacks:
                await self.request_callbacks[request_id](answer)
                del self.request_callbacks[request_id]
                return web.Response(text="OK")
            else:
                logger.warning(f"Callback not found for request {request_id}")
                return web.Response(text="Request ID not found", status=404)
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
        
        async def callback(answer: str):
            await context.session.generate_reply(instructions=f"My supervisor says: {answer}")
        
        self.request_callbacks[help_request.id] = callback
        logger.info(f"Registered callback for request {help_request.id}")
        
        try:
            self._sync_request_to_flask(help_request)
        except Exception as e:
            logger.error(f"Sync failed: {e}")
        
        return "I'll check with my supervisor and get back to you shortly."

    def _sync_request_to_flask(self, help_request):
        payload = {
            "id": help_request.id,
            "customer_id": help_request.customer_id,
            "question": help_request.question,
            "status": help_request.status,
            "webhook_url": help_request.webhook_url,
            "created_at": help_request.created_at.isoformat()
        }
        
        response = requests.post(f"{FLASK_API_URL}/api/sync-request", json=payload, timeout=5)
        if response.status_code != 200:
            logger.error(f"Sync failed: {response.text}")

async def entrypoint(ctx: JobContext):
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    
    try:
        logger.info("Connecting to LiveKit...")
        await ctx.connect()
        
        salon_agent = SalonAgent()
        await salon_agent.start_webhook_server()
        
        session = AgentSession(
            vad=silero.VAD.load(),
            stt=deepgram.STT(model="nova-3"),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=openai.TTS(voice="alloy"),
        )
        
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