# Task Caller — AI voice call dispatch

Type what you want an AI to do, pick a contact, hit send. The contact gets a
WhatsApp message with a call link; they tap it and talk to a live AI voice agent
that pursues your task. When the call ends you get a summary pushed back to your
WhatsApp.

No phone-network minutes, no app install for the contact — just a browser link
over WebRTC.

## Engineering highlights

- **Real-time speech↔speech** through a [pipecat](https://github.com/pipecat-ai/pipecat)
  pipeline wrapping Gemini Live (native-audio), with sub-second turn latency.
- **Agentic call termination** — the model is given an `end_call` tool and decides
  for itself when the task is done; a custom `EndCallController` frame processor
  hangs up the instant the spoken goodbye finishes (driven by `LLMFullResponseEndFrame`,
  not a fixed timer).
- **NAT traversal on a free tier** — the host sits behind CGNAT, so media is relayed
  through short-lived Twilio TURN credentials minted per call; signaling rides a
  public HTTPS tunnel. No paid PSTN, no port-forwarding.
- **Installable PWA** — the operator dashboard installs to an iPhone home screen
  (manifest + apple-touch meta) and runs fullscreen like a native app.
- **Resilient by design** — the WhatsApp gateway and summarizer never raise, so a
  provider outage degrades gracefully instead of crashing a live call; 23 unit tests
  cover the DB, gateway, summarizer, and HTTP layer.

> **Cost:** intentionally $0 to run. Real PSTN dialling (Telnyx/Twilio Voice) is a
> documented future option but deliberately not used — the WebRTC link + WhatsApp
> notification delivers the same UX for free.

---

## How it works

```
You (dashboard)                         Contact (phone browser)
      │  POST /control/jobs                     │  taps WhatsApp link
      ▼                                         ▼
┌─────────────────────── one FastAPI process (server.py, :7860) ───────────────────────┐
│  /control/*  dashboard + job API ── SQLite (jobs.db)                                  │
│  /           call page (static/index.html) ── WebRTC ── Gemini Live (speech↔speech)  │
│  on call end ── summarizer (Gemini Flash, text) ── push summary to your WhatsApp     │
└──────────────────────────────────────────────────────────────────────────────────────┘
      │ outbound WhatsApp                         ▲ ICE / media
      ▼                                           │
  OpenWA gateway (openwa/, Node, :3000)     Twilio TURN relay (crosses CGNAT / 3G)
  whatsapp-web.js → your WhatsApp           cloudflared tunnel → public HTTPS
```

The call server and dashboard are **one process**. The contact's call page lives
at `/` so links stay short; your dashboard is at `/control`.

---

## Components

| File | Role |
|------|------|
| `server.py` | FastAPI app: call page, WebRTC signaling, the pipecat voice pipeline, call→summary→notify |
| `control_app.py` | `/control` dashboard routes + job creation (normalizes the phone number) |
| `db.py` | SQLite job store (stdlib `sqlite3`, no ORM) |
| `summarizer.py` | Task-aware transcript summary via Gemini Flash (text) |
| `whatsapp.py` | Async HTTP client for the OpenWA gateway — never raises |
| `config.py` | All config; loads the Gemini key from the phone-call skill's `call_config.py` |
| `static/index.html` | The contact's call page (ring → answer → live call → done) |
| `static/control.html` | Your dashboard |
| `openwa/index.js` | Node WhatsApp gateway (whatsapp-web.js) — `POST /send`, `GET /health` |
| `deploy/` | Oracle Cloud VM bootstrap (supervisord, setup.sh, .env.example) |

---

## Prerequisites

- Python 3.12 (`.venv312`), deps: `pip install -r requirements.txt`
- Node 18+ for the WhatsApp gateway
- `cloudflared` (for a public HTTPS URL so phones can connect)
- A Gemini API key in `call_config.py` (or `GEMINI_API_KEY` env)
- Twilio account SID + auth token (TURN relay — needed for cross-network / 3G calls)

## Setup & run (local)

```bash
# 1. Python deps
.venv312/Scripts/pip install -r requirements.txt

# 2. WhatsApp gateway (one-time QR link)
cd openwa && npm install && node index.js     # scan qr.png in WhatsApp → Linked Devices

# 3. Public tunnel (phones can't reach localhost)
cloudflared tunnel --url http://localhost:7860   # copy the https URL

# 4. Call server  (TURN on for phone calls; LIVE_DOMAIN = the tunnel host)
LIVE_USE_TURN=1 LIVE_DOMAIN=<your-tunnel>.trycloudflare.com \
  .venv312/Scripts/python server.py
```

Open `http://localhost:7860/control`, enter a task + number, hit send.

> **Local-only test (same machine, no phone):** set `LIVE_USE_TURN=0` and open the
> `/?job=<id>` link in a browser on the same PC.

---

## Configuration (env vars)

| Var | Default | Purpose |
|-----|---------|---------|
| `LIVE_DOMAIN` | `localhost:7860` | Host used to build the call link sent over WhatsApp |
| `LIVE_USE_TURN` | `1` | `1` = relay media via Twilio TURN (cross-network / 3G); `0` = STUN only (same network) |
| `LIVE_MODEL` | `gemini-2.5-flash-native-audio-preview-12-2025` | Realtime speech model |
| `LIVE_VOICE` | `Charon` | Gemini Live voice |
| `SUMMARIZER_MODEL` | `models/gemini-2.5-flash` | Text model for the call summary |
| `MAX_SECONDS` | `300` | Hard call-duration cap |
| `ADAM_WHATSAPP` | — | Number that receives call summaries |
| `DEFAULT_COUNTRY_CODE` | `961` | Prepended to bare local numbers typed in the dashboard |
| `OPENWA_URL` | `http://localhost:3000` | WhatsApp gateway base URL |

Phone numbers are normalized in `control_app._normalize_contact`: a bare
`71234567` becomes `+96171234567`; numbers entered with a leading `+`, `00`, or
the country code are kept as-is.

---

## Call lifecycle

1. `POST /control/jobs` creates a job and WhatsApps the contact a `/?job=<id>` link.
2. Contact taps it → the page POSTs to `/start`, opens a WebRTC connection, and the
   bot greets first (a kickoff message 2s after connect).
3. The AI pursues the task. It has one tool, **`end_call`**: when its goal is met and
   the person confirms they're done, it says a short goodbye and calls `end_call`.
4. `EndCallController` watches for the end-of-turn signal (the goodbye finishing) and
   tears the call down promptly (`task.cancel()`), with a 6s safety-net fallback.
5. On teardown the transcript is saved, a task-aware summary is generated, and both
   are pushed to your WhatsApp.

### Why the hangup is built this way
`end_call` fires when the model *decides*, but the spoken goodbye is generated
*after*. So we don't hang up on the function call — we hang up `tail` seconds after
the real end-of-turn frame (`LLMFullResponseEndFrame` / `TTSStoppedFrame`), which
adapts to the goodbye's actual length. A graceful `EndFrame` was deliberately
avoided: its pipeline-flush + Gemini-websocket close added ~7s of lag.

---

## Deploy (Oracle Cloud Always-Free)

See `deploy/` — `setup.sh` bootstraps Ubuntu 22.04, `supervisord.conf` runs the
three processes (call server, OpenWA, cloudflared), `.env.example` is the config
template. A VM with a public IP + stable domain removes the ephemeral-tunnel churn
(quick tunnels get a new URL on every restart, which breaks already-sent links).

---

## Security notes

- Secrets are **never** hardcoded in tracked files — the Gemini key is read from
  `call_config.py` (git-ignored). See `.gitignore` before publishing.
- `openwa/.wwebjs_auth/` is the linked WhatsApp **session** — it grants full account
  access. It is git-ignored; never commit or share it.
- `jobs.db` and `transcripts/` contain call content and are git-ignored.
- The dashboard has no auth — the access control is "only people who can reach the
  host." Don't expose it on a public domain without adding auth.

---

## Tech stack

Python · FastAPI · pipecat · Gemini Live & Gemini Flash · WebRTC (aiortc) ·
SQLite · Node.js (whatsapp-web.js) · Twilio TURN · Tailscale Funnel

Built by **Adam Barbir**.

