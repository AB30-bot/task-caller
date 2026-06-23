"""Live tap-a-link AI voice call server.

Serves a browser page that opens a WebRTC mic/speaker connection to this process,
pipes the caller's audio to the Gemini Live realtime brain (speech-to-speech), and
streams the AI's voice back — sub-second. On hangup or the 5-minute cap, the
transcript is saved to transcripts/ and the DB job is updated with a task-aware summary.

Run:  .venv312\\Scripts\\python server.py   (then open http://localhost:7860)
"""
import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from loguru import logger

import config
import summarizer as summarizer_module
from db import Database
from whatsapp import WhatsAppClient
import control_app

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMMessagesAppendFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pipecat_ai_small_webrtc_prebuilt.frontend import SmallWebRTCPrebuiltUI

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Module-level singletons initialised in main() / startup
_db: Database = None
_wa: WhatsAppClient = None

# Maps pc_id → job_id so run_bot can look up the job without changing the pipecat callback signature
_job_by_pc_id: dict[str, str] = {}

# Strong references to fire-and-forget background tasks so the event loop doesn't GC them
_bg_tasks: set = set()


# --------------------------------------------------------------------------- #
# Transcript
# --------------------------------------------------------------------------- #
def _message_text(content) -> str:
    """Flatten an LLMContext message's content to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(p.get("text") or p.get("content") or "")
            else:
                parts.append(str(p))
        return " ".join(s for s in parts if s)
    return str(content)


def save_transcript(context: LLMContext) -> str | None:
    """Write the conversation to transcripts/<timestamp>.txt. Returns the path."""
    try:
        os.makedirs(config.TRANSCRIPT_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(config.TRANSCRIPT_DIR, f"{ts}.txt")
        lines = [f"Live AI call transcript — {ts}", ""]
        for m in context.messages:
            role = m.get("role", "?")
            if role == "system":
                continue  # don't dump the persona prompt into the transcript
            text = _message_text(m.get("content", "")).strip()
            if not text:
                continue
            if text == config.GREETING_PROMPT.strip():
                continue  # internal priming instruction, not a spoken turn
            if role == "user" and text.startswith("The call has just connected"):
                continue  # dynamic greeting kickoff prompt, not a spoken turn
            lines.append(f"{role}: {text}")
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Transcript saved: {path}")
        return path
    except Exception as e:  # never let transcript-saving crash the call teardown
        logger.error(f"Failed to save transcript: {e}")
        return None


def _extract_transcript_text(context: LLMContext) -> str:
    """Return the conversation as plain text (same filtering as save_transcript)."""
    lines = []
    for m in context.messages:
        role = m.get("role", "?")
        if role == "system":
            continue
        text = _message_text(m.get("content", "")).strip()
        if not text or text == config.GREETING_PROMPT.strip():
            continue
        if role == "user" and text.startswith("The call has just connected"):
            continue  # dynamic greeting kickoff prompt, not a spoken turn
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The bot: one pipeline per WebRTC connection
# --------------------------------------------------------------------------- #
class EndCallController(FrameProcessor):
    """Hangs up the call promptly once `end_call` decides it's over.

    `end_call` arms it; the next end-of-turn signal (LLMFullResponseEndFrame /
    TTSStoppedFrame — pushed when the model finishes its spoken goodbye) triggers
    teardown after a short tail so the client's last audio plays. A hard fallback
    caps how long it can linger. We use task.cancel() (immediate) rather than a
    graceful EndFrame, whose flush + Gemini-websocket close added ~7s of lag.
    """

    _END_TRIGGERS = (LLMFullResponseEndFrame, TTSStoppedFrame, BotStoppedSpeakingFrame)

    def __init__(self, task, tail: float = 0.8, fallback: float = 6.0):
        super().__init__()
        self._task = task
        self._tail = tail          # client audio-playout cushion after the goodbye
        self._fallback = fallback  # pure safety net: end-of-turn signals are reliable
        self._armed = False
        self._triggered = False    # a signal has scheduled the real hangup
        self._ended = False

    def arm(self):
        if self._armed:
            return
        self._armed = True
        logger.info(f"EndCallController armed (fallback={self._fallback}s)")
        self._spawn(self._fallback, "fallback")  # only fires if no signal arrives

    def _spawn(self, delay: float, why: str):
        async def _end():
            await asyncio.sleep(delay)
            if self._ended:
                return
            self._ended = True
            logger.info(f"Hanging up now (trigger={why}, after={delay}s)")
            await self._task.cancel()  # immediate teardown, not a graceful flush

        t = asyncio.create_task(_end())
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        # First end-of-turn signal after arming = the goodbye just finished. Take
        # over from the fallback and hang up after a short playout tail. Trigger
        # once — the service emits several end-of-turn frames per turn.
        if self._armed and not self._triggered and isinstance(frame, self._END_TRIGGERS):
            self._triggered = True
            logger.info(f"End-of-turn signal seen: {type(frame).__name__} — ending after {self._tail}s")
            self._spawn(self._tail, type(frame).__name__)


async def run_bot(webrtc_connection):
    """Build and run the voice pipeline for a single caller connection."""
    pc_id = getattr(webrtc_connection, "pc_id", None)
    job_id = _job_by_pc_id.pop(pc_id, None)
    job_task = None

    if job_id:
        job = _db.get_job(job_id)
        job_task = job["task"] if job else None
        logger.info(f"Starting bot for pc_id={pc_id} job_id={job_id[:8]}… task={job_task!r}")
    else:
        logger.info(f"Starting bot for pc_id={pc_id} (no job)")

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # `end_call` tool — lets the model hang up once its goal is met and the
    # person confirms they're done (it speaks a goodbye first, see system prompt).
    end_call_tool = FunctionSchema(
        name="end_call",
        description=(
            "End the phone call and hang up. Call this only AFTER you have spoken a brief "
            "goodbye out loud, and only once your goal is complete and the person has "
            "confirmed they have nothing else — or if they clearly want to stop."
        ),
        properties={
            "reason": {
                "type": "string",
                "description": "Short reason, e.g. 'task complete' or 'caller said goodbye'.",
            }
        },
        required=[],
    )

    llm = GeminiLiveLLMService(
        api_key=config.GEMINI_API_KEY,
        model=config.MODEL,
        voice_id=config.VOICE,
        system_instruction=config.build_system_instruction(job_task),
        tools=ToolsSchema(standard_tools=[end_call_tool]),
    )

    context = LLMContext([])
    aggregator = LLMContextAggregatorPair(context, realtime_service_mode=True)

    end_controller = EndCallController(task=None)  # task wired in after it's built

    pipeline = Pipeline([
        transport.input(),
        aggregator.user(),
        llm,
        transport.output(),
        end_controller,        # watches for the bot finishing its goodbye
        aggregator.assistant(),
    ])

    task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True))
    end_controller._task = task  # now that the task exists

    async def _handle_end_call(params):
        """Model decided the call is done — hang up the moment the goodbye finishes."""
        reason = (params.arguments or {}).get("reason", "unspecified")
        logger.info(f"AI requested end_call (reason={reason!r}) — arming reactive hangup")
        await params.result_callback({"status": "ending"})
        end_controller.arm()

    llm.register_function("end_call", _handle_end_call)

    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport, _client):
        if job_id:
            _db.update_job(job_id,
                status="live",
                call_started_at=datetime.now(timezone.utc).isoformat(),
            )
        logger.info("Caller connected — scheduling greeting kickoff")

        async def _kickoff():
            await asyncio.sleep(2.0)
            await task.queue_frames([
                LLMMessagesAppendFrame(
                    messages=[{"role": "user", "content": config.build_greeting_prompt(job_task)}],
                    run_llm=True,
                )
            ])
            logger.info("Greeting kickoff sent")

        t = asyncio.create_task(_kickoff())
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport, _client):
        logger.info("Caller disconnected — ending call")
        await task.cancel()

    async def _duration_cap():
        try:
            await asyncio.sleep(config.MAX_SECONDS)
            logger.info(f"Reached {config.MAX_SECONDS}s cap — ending call")
            await task.queue_frame(EndFrame())
        except asyncio.CancelledError:
            pass

    cap_task = asyncio.create_task(_duration_cap())
    runner = PipelineRunner(handle_sigint=False)

    try:
        await runner.run(task)
    finally:
        cap_task.cancel()
        transcript_text = _extract_transcript_text(context)
        save_transcript(context)

        if job_id:
            _db.update_job(job_id,
                status="done",
                call_ended_at=datetime.now(timezone.utc).isoformat(),
                transcript=transcript_text,
            )
            if job_task:
                t = asyncio.create_task(
                    _summarize_and_notify(job_id, job_task, transcript_text)
                )
                _bg_tasks.add(t)
                t.add_done_callback(_bg_tasks.discard)


async def _summarize_and_notify(job_id: str, task: str, transcript: str):
    """Generate a task-aware summary and push it to Adam's WhatsApp."""
    summary = ""
    if transcript.strip():
        summary = await summarizer_module.summarize(
            task=task,
            transcript=transcript,
            api_key=config.GEMINI_API_KEY,
            model=config.SUMMARIZER_MODEL,
        )
    if summary:
        _db.update_job(job_id, summary=summary)
        logger.info(f"Summary saved for job {job_id[:8]}")

    job = _db.get_job(job_id)
    contact = job["contact"] if job else "unknown"
    duration = "?"
    if job and job.get("call_started_at") and job.get("call_ended_at"):
        start = datetime.fromisoformat(job["call_started_at"])
        end = datetime.fromisoformat(job["call_ended_at"])
        secs = int((end - start).total_seconds())
        duration = f"{secs // 60}:{secs % 60:02d}"

    # Put the full resume right here in WhatsApp — no dashboard needed.
    body = summary if summary else "Call ended — no conversation was captured."
    msg = (
        f"✓ Call with {contact} ended ({duration}).\n"
        f"📋 Task: {task}\n\n"
        f"{body}"
    )
    await _wa.send(config.ADAM_WHATSAPP, msg)


# --------------------------------------------------------------------------- #
# FastAPI app: serve the page + WebRTC signaling
# --------------------------------------------------------------------------- #
app = FastAPI()
app.mount("/client", SmallWebRTCPrebuiltUI)
# Serve PWA assets (manifest, app icons) for the installable dashboard.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_STUN_FALLBACK_SERVER = [IceServer(urls="stun:stun.l.google.com:19302")]
_STUN_FALLBACK_CLIENT = [{"urls": ["stun:stun.l.google.com:19302"]}]
_handler = SmallWebRTCRequestHandler(ice_servers=_STUN_FALLBACK_SERVER)

# TURN relay (Twilio NTS) — cached short-lived ICE servers for both peers.
_ice_cache = {"server": None, "client": None, "exp": 0.0}
_ice_lock = asyncio.Lock()


async def _fetch_twilio_ice():
    """Mint short-lived STUN+TURN servers from Twilio's Network Traversal Service."""
    sid, tok = config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN
    if not (sid and tok):
        raise RuntimeError("no Twilio creds")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Tokens.json"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, auth=aiohttp.BasicAuth(sid, tok)) as r:
            r.raise_for_status()
            data = await r.json()
    server, client = [], []
    for e in data.get("ice_servers", []):
        urls = e.get("urls") or e.get("url")
        if not urls:
            continue
        user, cred = e.get("username"), e.get("credential")
        server.append(IceServer(urls=urls, username=user, credential=cred))
        item = {"urls": urls}
        if user:
            item["username"] = user
            item["credential"] = cred
        client.append(item)
    if not server:
        raise RuntimeError("Twilio returned no ice_servers")
    return server, client, int(data.get("ttl", 86400))


async def get_ice_servers():
    """Return (server_side IceServers, client_side dicts), cached until near expiry."""
    async with _ice_lock:
        if not config.USE_TURN:
            return _STUN_FALLBACK_SERVER, _STUN_FALLBACK_CLIENT
        if _ice_cache["server"] and time.time() < _ice_cache["exp"]:
            return _ice_cache["server"], _ice_cache["client"]
        try:
            server, client, ttl = await _fetch_twilio_ice()
            has_turn = any("turn:" in str(getattr(s, "urls", "")) for s in server)
            logger.info(f"TURN: fetched {len(server)} ICE servers from Twilio (turn={has_turn})")
        except Exception as e:
            logger.warning(f"TURN: Twilio fetch failed ({e}); STUN-only fallback (cross-network calls may fail)")
            server, client, ttl = _STUN_FALLBACK_SERVER, _STUN_FALLBACK_CLIENT, 300
        _ice_cache.update(server=server, client=client, exp=time.time() + max(60, ttl - 300))
        return server, client


@app.on_event("startup")
async def _startup():
    global _db, _wa
    _db = Database(config.DB_PATH)
    _wa = WhatsAppClient(config.OPENWA_URL)
    control_app.init(_db, _wa, config)
    app.include_router(control_app.router)
    await get_ice_servers()


# Sessions created by /start. The prebuilt client (v2.5.0) does the unified flow:
#   POST /start                       -> { sessionId, iceConfig }
#   POST /sessions/<id>/api/offer     -> WebRTC offer  (PATCH for ICE candidates)
_active_sessions: dict[str, dict] = {}


@app.get("/", include_in_schema=False)
async def root():
    """Serve the custom branded call page (the friend's tap-a-link landing)."""
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return RedirectResponse(url="/client/")  # fallback to prebuilt UI


@app.post("/start")
async def start(request: Request):
    """Begin a WebRTC session; the bot starts when the offer arrives."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    session_id = str(uuid.uuid4())
    job_id = data.get("job_id")
    _active_sessions[session_id] = {"job_id": job_id}

    server_ice, client_ice = await get_ice_servers()
    _handler.update_ice_servers(server_ice)

    return {"sessionId": session_id, "iceConfig": {"iceServers": client_ice}}


async def _offer(
    request: SmallWebRTCRequest,
    background_tasks: BackgroundTasks,
    job_id: str | None = None,
):
    async def _cb(connection):
        if job_id and hasattr(connection, "pc_id"):
            _job_by_pc_id[connection.pc_id] = job_id
        background_tasks.add_task(run_bot, connection)

    return await _handler.handle_web_request(request=request, webrtc_connection_callback=_cb)


@app.post("/api/offer")
async def offer(request: SmallWebRTCRequest, background_tasks: BackgroundTasks):
    return await _offer(request, background_tasks)


@app.patch("/api/offer")
async def patch(request: SmallWebRTCPatchRequest):
    await _handler.handle_patch_request(request)
    return {"status": "success"}


@app.api_route(
    "/sessions/{session_id}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def session_proxy(
    session_id: str, path: str, request: Request, background_tasks: BackgroundTasks
):
    """Route /sessions/<id>/api/offer to the WebRTC handler (the client's offer URL)."""
    if session_id not in _active_sessions:
        return Response(content="Invalid or not-yet-ready session_id", status_code=404)

    if path.endswith("api/offer"):
        try:
            data = await request.json()
        except Exception:
            return Response(content="Invalid WebRTC request", status_code=400)

        if request.method == "POST":
            req = SmallWebRTCRequest(
                sdp=data["sdp"],
                type=data["type"],
                pc_id=data.get("pc_id"),
                restart_pc=data.get("restart_pc"),
                request_data=data.get("request_data") or _active_sessions[session_id],
            )
            job_id = _active_sessions.get(session_id, {}).get("job_id")
            return await _offer(req, background_tasks, job_id=job_id)
        if request.method == "PATCH":
            preq = SmallWebRTCPatchRequest(
                pc_id=data["pc_id"],
                candidates=[IceCandidate(**c) for c in data.get("candidates", [])],
            )
            await _handler.handle_patch_request(preq)
            return {"status": "success"}

    return Response(status_code=200)


def main():
    logger.remove()
    logger.add(sys.stderr, level=os.environ.get("LOG_LEVEL", "INFO"))
    if not config.GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is empty — set it or fix call_config.py path.")
        sys.exit(1)
    logger.info(f"Brain model: {config.MODEL}  voice: {config.VOICE}  cap: {config.MAX_SECONDS}s")
    logger.info(f"Open http://localhost:{config.PORT}")
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    main()
