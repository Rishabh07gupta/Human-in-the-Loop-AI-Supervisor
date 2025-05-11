import asyncio
import json
import logging
from datetime import datetime
import ssl
import os
import uuid
import certifi
import requests
import aiohttp
from typing import Optional
from aiohttp import web
from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, RunContext, WorkerOptions, cli, function_tool
from livekit.plugins import deepgram, openai, silero
from modules.help_requests import create_help_request, get_knowledge_for_question 
from modules.knowledge_base import get_salon_info_standalone, init_sample_salon_data, add_to_knowledge_base 
from persistent_callbacks import callback_registry

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

FLASK_API_URL = os.environ.get("FLASK_API_URL", "http://localhost:5000")
AGENT_WEBHOOK_BASE_URL = os.environ.get("AGENT_WEBHOOK_BASE_URL", "http://localhost:5001")

active_livekit_sessions: dict[str, AgentSession] = {}


def init_agent_dependencies():
    """
    Initializes dependencies that the agent might need, like sample data.
    In production, this should ideally be handled outside the agent's runtime,
    e.g., via DB migrations or admin setup.
    """
    logger.info("Initializing agent dependencies (sample data)...")
    logger.info("Agent dependencies initialization complete.")


class SalonAgent(Agent):
    def __init__(self):
        salon_info_str = get_salon_info_standalone() # Using standalone to avoid DB context issues in agent __init__
        logger.info("Retrieved salon info for agent instructions.")
        
        instructions = f"""You are Bella, the AI receptionist for Elegant Beauty Salon & Spa. Your role is to provide exceptional customer service while strictly adhering to these protocols:

            # CORE OPERATING FRAMEWORK
            1. INFORMATION ACCURACY
            - Only provide information explicitly contained in the salon details.
            - Never extrapolate, estimate, or guess.
            - IMPORTANT: For any uncertain information, IMMEDIATELY use the request_help function.

            2. CONVERSATIONAL PROTOCOLS
            - Maintain a warm, professional tone.
            - Use natural salon terminology.
            - Anticipate follow-up questions.

            3. ESCALATION TRIGGERS
            Immediately use request_help WITHOUT saying "Let me check that for you" first for:
            - Any service/pricing not explicitly listed in your knowledge.
            - Appointment availability requests (unless you have a direct API for this).
            - Complex service combinations.
            - Special requests (allergies, disabilities).
            - Complaints or sensitive situations.
            - ANY questions about discounts, promotions, or pricing exceptions not in your knowledge.
            
            4. CRITICAL PROCEDURE
            - NEVER say "Let me check that for you" and then do nothing.
            - If you need to check, call request_help function FIRST, then inform the customer based on its output.

            # SALON KNOWLEDGE BASE (Summary - detailed queries go through request_help or tools)
            {self._format_salon_info_for_prompt(salon_info_str)}

            # Tool Usage
            - Use 'request_help' to escalate questions you cannot answer from your current knowledge.
            """
        
        super().__init__(instructions=instructions)
        self.agent_instance_id = str(uuid.uuid4()) 
        self.webhook_server_runner: Optional[web.AppRunner] = None # For managing the aiohttp server
        logger.info(f"SalonAgent instance {self.agent_instance_id} created.")

    def _format_salon_info_for_prompt(self, info_str: str) -> str:
        """Formats salon info for the LLM prompt, making it more structured."""
        return "\n".join([
            "=== SALON DETAILS ===",
            info_str.replace(":", ":\n  - ").replace("\n\n", "\n").strip(),
            "====================="
        ])

    async def _start_webhook_server(self):
        """Starts the aiohttp server for receiving resolved answers from Flask app."""
        if self.webhook_server_runner:
            logger.info("Webhook server already running or initialized.")
            return

        app = web.Application()
        app.router.add_post('/webhook/resolved/{request_id}', self._handle_resolved_webhook_http)
        runner = web.AppRunner(app)
        await runner.setup()

        try:
            webhook_host = AGENT_WEBHOOK_BASE_URL.split("://")[1].split(":")[0]
            webhook_port = int(AGENT_WEBHOOK_BASE_URL.split(":")[-1].split("/")[0])
        except Exception as e:
            logger.error(f"Could not parse host/port from AGENT_WEBHOOK_BASE_URL ('{AGENT_WEBHOOK_BASE_URL}'): {e}. Defaulting to localhost:5001")
            webhook_host = 'localhost'
            webhook_port = 5001

        site = web.TCPSite(runner, webhook_host, webhook_port)
        try:
            await site.start()
            self.webhook_server_runner = runner 
            logger.info(f"Agent webhook server started on {webhook_host}:{webhook_port}")
        except OSError as e:
             logger.error(f"Failed to start agent webhook server on {webhook_host}:{webhook_port}: {e} (Address already in use?)")
             self.webhook_server_runner = None

    async def _stop_webhook_server(self):
        """Stops the aiohttp server gracefully."""
        if self.webhook_server_runner:
            logger.info("Stopping agent webhook server...")
            await self.webhook_server_runner.cleanup()
            self.webhook_server_runner = None
            logger.info("Agent webhook server stopped.")

    def register_livekit_session(self, lk_session: AgentSession):
        """Registers the LiveKit AgentSession for potential callbacks from webhook."""
        active_livekit_sessions[self.agent_instance_id] = lk_session
        logger.info(f"LiveKit AgentSession registered for agent instance {self.agent_instance_id}.")

    def unregister_livekit_session(self):
        """Unregisters the LiveKit AgentSession"""
        removed_session = active_livekit_sessions.pop(self.agent_instance_id, None)
        if removed_session:
            logger.info(f"LiveKit AgentSession unregistered for agent instance {self.agent_instance_id}.")
        else:
            logger.warning(f"Attempted to unregister LiveKit AgentSession for agent {self.agent_instance_id}, but none was found.")


    async def _handle_resolved_webhook_http(self, http_request: web.Request):
        """Handles incoming HTTP POST from Flask app when a request is resolved."""
        try:
            request_id_str = http_request.match_info.get('request_id')
            if not request_id_str:
                logger.warning("Webhook received without request_id in path.")
                return web.Response(text="Missing request_id in path", status=400)
            
            request_id = int(request_id_str)
            data = await http_request.json()
            answer = data.get('answer')
            
            if not answer:
                logger.warning(f"Webhook for request_id {request_id} missing 'answer'. Data: {data}")
                return web.Response(text="Missing answer in JSON payload", status=400)
            
            agent_id_for_callback = callback_registry.get_session_for_request(request_id)
            
            if not agent_id_for_callback:
                logger.warning(f"No agent_instance_id found in callback_registry for help_request_id {request_id}. Cannot route reply.")
                return web.Response(text=f"Agent session for request_id {request_id} not found in registry", status=404)

            lk_session_to_reply = active_livekit_sessions.get(agent_id_for_callback)
            
            if not lk_session_to_reply:
                logger.warning(f"LiveKit AgentSession for agent_id '{agent_id_for_callback}' (from request_id {request_id}) not found in active_livekit_sessions. Cannot generate reply.")
                return web.Response(text=f"LiveKit session for agent {agent_id_for_callback} not active", status=404)
            
            logger.info(f"Received resolved answer for help_request_id {request_id} via webhook. Answer: '{answer[:50]}...'. Routing to LiveKit session for agent {agent_id_for_callback}.")

            reply_prompt = f"My supervisor has provided an answer to your question: {answer}. Please relay this to the customer."
            await lk_session_to_reply.generate_reply(instructions=reply_prompt)
            
            callback_registry.remove(request_id) 
            logger.info(f"Successfully processed webhook for request_id {request_id} and sent reply to customer.")
            return web.Response(text="OK", status=200)
                
        except ValueError:
            logger.error(f"Webhook received with invalid non-integer request_id: {request_id_str}")
            return web.Response(text="Invalid request_id format", status=400)
        except json.JSONDecodeError:
            logger.error("Webhook received non-JSON payload or malformed JSON.")
            return web.Response(text="Invalid JSON payload", status=400)
        except Exception as e:
            logger.error(f"Error in agent's _handle_resolved_webhook_http: {e}", exc_info=True)
            return web.Response(text=f"Internal server error processing webhook: {str(e)}", status=500)


    @function_tool
    async def request_help(self, question: str, run_context: RunContext):
        """
        Use this tool to escalate a customer's question to a human supervisor
        when agent cannot find the answer in your current knowledge.
        This will create a help request. Inform the user you are checking with a supervisor.
        """
        customer_id = "unknown_customer"
        if run_context and hasattr(run_context, 'room') and run_context.room:
            customer_id = run_context.room.name 
        elif run_context and hasattr(run_context, 'participant') and run_context.participant:
            customer_id = run_context.participant.sid
        else: 
            customer_id = f"cust-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        logger.info(f"Agent {self.agent_instance_id} initiating 'request_help' for customer '{customer_id}'. Question: '{question[:70]}...'")
        
        # 1. Check internal knowledge base via Flask API
        knowledge_api_result = get_knowledge_for_question(question)
        
        if knowledge_api_result and hasattr(knowledge_api_result, 'answer'):
            logger.info(f"Found answer in knowledge base via API for '{question[:50]}...'. Answer: '{knowledge_api_result.answer[:50]}...'")
            return f"I found this information: {knowledge_api_result.answer}"
        
        # 2. If not found, create a help request and sync to Flask app
        logger.info(f"No direct answer found by get_knowledge_for_question. Creating help request for: '{question[:70]}...'")
        
        agent_callback_url = f"{AGENT_WEBHOOK_BASE_URL}/webhook/resolved"
        
        help_request_details = {
            "customer_id": customer_id,
            "question": question,
            "webhook_url": agent_callback_url, 
            "created_at": datetime.utcnow().isoformat()
        }

        flask_sync_response = await self._sync_request_to_flask_api(help_request_details)

        if flask_sync_response and flask_sync_response.get('success'):
            synced_help_request_id = flask_sync_response.get('id')
            if synced_help_request_id:
                # Register this help_request_id with the current agent_instance_id for callback routing
                callback_registry.register(synced_help_request_id, self.agent_instance_id)
                logger.info(f"Help request (ID: {synced_help_request_id}) created successfully via Flask API and registered for callback to agent {self.agent_instance_id}.")
                return "I'm checking with my supervisor on that question for you and will get back as soon as I have an update."
            else:
                logger.error("Flask API sync successful but no help_request_id returned. Cannot register callback.")
                return "I tried to check with my supervisor, but there was an issue. Please try asking again later."
        else:
            error_msg = flask_sync_response.get('error', 'unknown error') if flask_sync_response else "no response"
            logger.error(f"Failed to sync help request to Flask API: {error_msg}")
            return "I'm having a little trouble reaching my supervisor right now. Could you please ask again in a few moments?"


    async def _sync_request_to_flask_api(self, help_request_payload: dict) -> Optional[dict]:
        """Sends the help request details to the Flask app's API to be stored in DB."""
        sync_url = f"{FLASK_API_URL}/api/sync-request"
        logger.info(f"Agent {self.agent_instance_id} syncing help request to Flask API: {sync_url} with payload: {help_request_payload}")
        
        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(sync_url, json=help_request_payload, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    response_status = response.status
                    response_text = await response.text() 
                    if response.ok:
                        response_data = await response.json()
                        logger.info(f"Successfully synced help request to Flask API. Response: {response_data}")
                        return response_data
                    else:
                        logger.error(f"Flask API sync failed. Status: {response_status}, Body: {response_text}")
                        return {"success": False, "error": f"API error status {response_status}", "details": response_text}
        except aiohttp.ClientError as e_aio:
            logger.error(f"AIOHTTP ClientError during Flask API sync: {e_aio}")
            return {"success": False, "error": f"Network or client error: {str(e_aio)}"}
        except asyncio.TimeoutError:
            logger.error("Flask API sync request timed out.")
            return {"success": False, "error": "Request to supervisor system timed out."}
        except Exception as e:
            logger.error(f"Unexpected error during Flask API sync: {e}", exc_info=True)
            return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}


async def job_entrypoint(ctx: JobContext):
    """Entrypoint for the LiveKit agent job."""
    salon_agent = SalonAgent() # Create an instance of our agent

    try:
        logger.info(f"Agent {salon_agent.agent_instance_id} connecting to LiveKit room: {ctx.room.name}")
        await ctx.connect()
        logger.info(f"Agent {salon_agent.agent_instance_id} connected to LiveKit successfully.")
        
        # Start the agent's own webhook server for receiving callbacks from Flask
        await salon_agent._start_webhook_server()
        
        lk_session = AgentSession(
            vad=silero.VAD.load(),
            stt=deepgram.STT(model="nova-2"), 
            llm=openai.LLM(model="gpt-4o-mini"), 
            tts=openai.TTS(voice="alloy"),
        )
        salon_agent.register_livekit_session(lk_session)
        
        await lk_session.start(agent=salon_agent, room=ctx.room)
        logger.info(f"LiveKit AgentSession started for agent {salon_agent.agent_instance_id} in room: {ctx.room.name}")
        
        initial_greeting = (
            "Hello and welcome to Elegant Beauty Salon & Spa! I'm Bella, your AI receptionist. How can I help you today? "
            "Remember, for questions about appointment availability, complex services, or specific pricing not immediately known, "
            "I'll check with my supervisor."
        )
        await lk_session.generate_reply(instructions=initial_greeting)
        
        # Keep the job alive while the session is active.
        while ctx.room.connection_state != rtc.ConnectionState.CONN_DISCONNECTED:
            await asyncio.sleep(10)
        logger.info(f"LiveKit AgentSession for agent {salon_agent.agent_instance_id} in room {ctx.room.name} is no longer active.")
            
    except Exception as e:
        logger.error(f"Error in agent job_entrypoint for agent {salon_agent.agent_instance_id}: {e}", exc_info=True)
        raise
    finally:
        logger.info(f"Agent job for {salon_agent.agent_instance_id} ending. Cleaning up resources...")
        # Cleanup: Stop webhook server, unregister session
        await salon_agent._stop_webhook_server()
        salon_agent.unregister_livekit_session()
        logger.info(f"Cleanup complete for agent {salon_agent.agent_instance_id}.")


def run_agent_worker():
    """Configures and runs the LiveKit agent worker."""
    # Ensuring essential environment variables for AI services are set
    required_env_vars = ['OPENAI_API_KEY', 'DEEPGRAM_API_KEY', 'LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET']
    for var in required_env_vars:
        if not os.getenv(var):
            logger.error(f"CRITICAL: Environment variable {var} is not set. Agent may not function correctly.")
    os.environ["SSL_CERT_FILE"] = certifi.where() 
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

    worker_options = WorkerOptions(
        entrypoint_fnc=job_entrypoint,
    )
    cli.run_app(worker_options)


if __name__ == "__main__":
    logger.info("Starting Salon AI Agent Worker...")
    run_agent_worker()