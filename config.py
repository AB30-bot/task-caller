"""Configuration for the live tap-a-link AI voice caller.

Secrets are NOT hardcoded here: the Gemini key is read from the phone-call skill's
call_config.py (or the GEMINI_API_KEY env var). This file is safe-ish to read but
is git-ignored anyway.
"""
import importlib.util
import os

# Load a local .env (used on the VM deploy) so os.environ picks up secrets/config.
# No-op if the file is absent (e.g. local dev reads call_config.py directly).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass

# --- Gemini key (read, don't hardcode) ---
# On Windows dev this skill-local path holds the key; on the Linux VM it won't
# exist, so the GEMINI_API_KEY env var (from .env) is used instead.
_PHONE_CALL_CONFIG = r"C:\Users\User\.claude\skills\phone-call\call_config.py"


def _load_gemini_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    try:
        spec = importlib.util.spec_from_file_location("call_config", _PHONE_CALL_CONFIG)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "GEMINI_API_KEY", "")
    except Exception:
        return ""


GEMINI_API_KEY = _load_gemini_key()


# --- TURN relay creds (Twilio Network Traversal Service) ---
# Read from the same call_config.py (or env). Used server-side to mint short-lived
# TURN credentials so the call's audio can cross networks (the tunnel only carries
# signaling). User approved using their Twilio account for this.
def _load_twilio():
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    tok = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if sid and tok:
        return sid, tok
    try:
        spec = importlib.util.spec_from_file_location("call_config", _PHONE_CALL_CONFIG)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "TWILIO_ACCOUNT_SID", ""), getattr(mod, "TWILIO_AUTH_TOKEN", "")
    except Exception:
        return "", ""


TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN = _load_twilio()

# TURN relay on/off. Needed for calls across the internet (friend on cellular),
# but Twilio TURN rejects private/localhost peer IPs (403), so disable it for
# local/same-network testing: set LIVE_USE_TURN=0.
USE_TURN = os.environ.get("LIVE_USE_TURN", "1") != "0"

# --- Server ---
PORT = int(os.environ.get("PORT", "7860"))
HOST = "0.0.0.0"  # bind all interfaces so the cloudflared tunnel can reach it

# --- Brain (confirmed available on the key by the Task 1 spike) ---
# Default = native-audio. This is the model that AUDIBLY worked in Tier-1 (user heard it).
# Headless energy testing showed gemini-3.1-flash-live-preview produces ONLY SILENCE through
# this pipeline (maxamp=0, empty transcript) — so it's NOT usable here despite greeting-first
# claims. Native-audio emits real speech; trade-off is the caller usually speaks first.
MODEL = os.environ.get("LIVE_MODEL", "models/gemini-2.5-flash-native-audio-preview-12-2025")
VOICE = os.environ.get("LIVE_VOICE", "Charon")  # Gemini Live voices: Puck, Charon, Kore, Fenrir, Aoede...

# --- Call shape ---
MAX_SECONDS = int(os.environ.get("MAX_SECONDS", "300"))  # 5-minute hard cap

# Persona + goal. The model MUST identify as an AI up front (locked decision).
GOAL = os.environ.get(
    "CALL_GOAL",
    "have a short, friendly check-in conversation and ask how the person is doing today",
)

SYSTEM_INSTRUCTION = f"""You are Adam's AI assistant — a warm, concise AI voice agent \
speaking on behalf of Adam (a person based in Lebanon).

CRITICAL: At the very start of the call, clearly introduce yourself and be upfront that \
you are an AI — for example: "Hi! This is Adam's AI assistant. Just so you know, I'm an AI, \
not a real person." Never pretend to be human.

This is a live, phone-style voice call, so speak naturally and keep your turns short \
(one or two spoken sentences at a time). Listen, and let the person talk.

Your goal for this call: {GOAL}.

Be friendly and respectful. If the person asks to stop or says goodbye, thank them warmly \
and end the conversation."""

# Message that kicks off the call: prompts the model to greet first.
GREETING_PROMPT = (
    "The call has just connected. Greet the person now: introduce yourself as Adam's AI "
    "assistant, make clear you are an AI, and then begin the conversation toward your goal."
)

# Where transcripts are written.
TRANSCRIPT_DIR = os.path.join(os.path.dirname(__file__), "transcripts")

# --- Platform config ---
# Domain served by cloudflared tunnel (no https://, no trailing slash).
# e.g. "abc.trycloudflare.com"  or your custom domain
DOMAIN = os.environ.get("LIVE_DOMAIN", "localhost:7860")

# OpenWA gateway URL (Node.js process, usually localhost)
OPENWA_URL = os.environ.get("OPENWA_URL", "http://localhost:3000")

# Operator's WhatsApp number (receives call summaries). Set via env / .env.
ADAM_WHATSAPP = os.environ.get("ADAM_WHATSAPP", "")

# Default country code for bare local numbers typed in the dashboard.
# So "71234567" becomes "+96171234567" automatically.
DEFAULT_COUNTRY_CODE = os.environ.get("DEFAULT_COUNTRY_CODE", "961")

# SQLite database path
DB_PATH = os.path.join(os.path.dirname(__file__), "jobs.db")

# Model for the text summarizer (cheap + fast, not the live audio model).
# NOTE: gemini-2.0-flash was retired (404). gemini-2.5-flash is current & confirmed working.
SUMMARIZER_MODEL = os.environ.get("SUMMARIZER_MODEL", "models/gemini-2.5-flash")


def build_system_instruction(task: str | None = None) -> str:
    goal = task if task else GOAL
    return (
        f"You are Adam's AI assistant — a warm, concise AI voice agent "
        f"speaking on behalf of Adam (a person based in Lebanon).\n\n"
        f"CRITICAL: At the very start of the call, clearly introduce yourself and be upfront that "
        f"you are an AI — for example: \"Hi! This is Adam's AI assistant. Just so you know, I'm an AI, "
        f"not a real person.\" Never pretend to be human.\n\n"
        f"This is a live, phone-style voice call, so speak naturally and keep your turns short "
        f"(one or two spoken sentences at a time). Listen, and let the person talk.\n\n"
        f"Your goal for this call: {goal}.\n\n"
        f"Be friendly and respectful. "
        f"ENDING THE CALL: Once you have accomplished your goal, ask the person if there is "
        f"anything else they need. When they indicate they are done (or if they ask to stop or "
        f"say goodbye), say ONE very short goodbye line out loud — just a few words, like "
        f"\"Okay, take care — bye!\" — and then immediately call the `end_call` function. "
        f"Keep the goodbye to a single short sentence so the call ends promptly; never call "
        f"end_call silently mid-conversation."
    )


def build_greeting_prompt(task: str | None = None) -> str:
    task_line = f" Your goal for this call: {task}" if task else ""
    return (
        f"The call has just connected. Greet the person now: introduce yourself as Adam's AI "
        f"assistant, make clear you are an AI, and then begin the conversation toward your goal.{task_line}"
    )
