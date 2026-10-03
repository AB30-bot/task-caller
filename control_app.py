import hmac
import os
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

MAX_TASK_LEN = 500
MIN_DIGITS, MAX_DIGITS = 7, 15  # E.164 allows at most 15 digits


def require_token(request: Request) -> None:
    """Guard the dashboard API with CONTROL_TOKEN (if configured).

    Accepts `Authorization: Bearer <t>`, `X-Control-Token: <t>` or `?token=<t>`.
    With no token configured the API stays open (local dev) — server.py warns.
    """
    expected = getattr(_cfg, "CONTROL_TOKEN", "")
    if not expected:
        return
    auth = request.headers.get("authorization", "")
    given = (
        auth[7:] if auth.lower().startswith("bearer ")
        else request.headers.get("x-control-token") or request.query_params.get("token", "")
    )
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")


router = APIRouter(prefix="/control")
protected = APIRouter(dependencies=[Depends(require_token)])

_db = None
_wa = None
_cfg = None


def init(db, wa, cfg):
    global _db, _wa, _cfg
    _db, _wa, _cfg = db, wa, cfg


def _normalize_contact(raw: str, default_cc: str = "1") -> str:
    """Turn whatever was typed into +<E.164>. A bare local number like
    a bare local number (e.g. '5551234') gets the default country code; '+<cc>…',
    '00<cc>…' and '<cc>…' are all accepted as-is."""
    s = (raw or "").strip()
    digits = re.sub(r"\D", "", s)
    if not digits:
        return s
    if s.startswith("+"):
        return "+" + digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    if digits.startswith(default_cc):
        return "+" + digits
    return f"+{default_cc}{digits.lstrip('0')}"  # bare local: drop trunk 0, add CC


def call_link_error(job: dict | None, ttl_hours: float, now: datetime | None = None) -> tuple[int, str] | None:
    """Why a call link can't be used, as (http_status, message) — or None if it's fine.

    A link works once (status must still be 'waiting') and expires after `ttl_hours`.
    """
    if not job:
        return 404, "This call link is not valid."
    if job["status"] != "waiting":
        return 410, "This call has already happened."
    now = now or datetime.now(timezone.utc)
    if now - datetime.fromisoformat(job["created_at"]) > timedelta(hours=ttl_hours):
        return 410, "This call link has expired."
    return None


def _valid_contact(contact: str) -> bool:
    return MIN_DIGITS <= len(re.sub(r"\D", "", contact)) <= MAX_DIGITS


class JobCreate(BaseModel):
    task: str
    contact: str


@router.get("", include_in_schema=False)
async def control_ui():
    path = os.path.join(os.path.dirname(__file__), "static", "control.html")
    return FileResponse(path)


@protected.get("/jobs")
async def list_jobs():
    return _db.list_jobs()


@protected.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = _db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def _send_link(job: dict) -> dict:
    """WhatsApp the call link for `job` and record when it went out."""
    scheme = "https" if "localhost" not in _cfg.DOMAIN else "http"
    call_url = f"{scheme}://{_cfg.DOMAIN}/?job={job['id']}"
    msg = (
        f"Hey! Adam asked his AI assistant to call you.\n"
        f"Tap the link to answer: {call_url}\n\n"
        f"(This is an AI, not a real person)"
    )
    sent = await _wa.send(job["contact"], msg)
    _db.update_job(
        job["id"],
        link_sent_at=datetime.now(timezone.utc).isoformat() if sent else None,
    )
    return _db.get_job(job["id"])


@protected.post("/jobs", status_code=201)
async def create_job(body: JobCreate):
    task = body.task.strip()
    if not task:
        raise HTTPException(status_code=422, detail="Task cannot be empty")
    if len(task) > MAX_TASK_LEN:
        raise HTTPException(status_code=422, detail=f"Task is too long (max {MAX_TASK_LEN} characters)")
    if not body.contact.strip():
        raise HTTPException(status_code=422, detail="Contact cannot be empty")

    cc = getattr(_cfg, "DEFAULT_COUNTRY_CODE", "1")
    contact = _normalize_contact(body.contact, cc)
    if not _valid_contact(contact):
        raise HTTPException(status_code=422, detail="That doesn't look like a valid phone number")

    return await _send_link(_db.create_job(task=task, contact=contact))


@protected.post("/jobs/{job_id}/resend")
async def resend_link(job_id: str):
    """Re-send the WhatsApp link (e.g. the first send failed). Only for unanswered jobs."""
    job = _db.get_job(job_id)
    err = call_link_error(job, getattr(_cfg, "LINK_TTL_HOURS", 24))
    if err:
        raise HTTPException(status_code=err[0], detail=err[1])
    return await _send_link(job)


router.include_router(protected)
