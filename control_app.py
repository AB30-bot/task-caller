import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(prefix="/control")

_db = None
_wa = None
_cfg = None


def init(db, wa, cfg):
    global _db, _wa, _cfg
    _db, _wa, _cfg = db, wa, cfg


def _normalize_contact(raw: str, default_cc: str = "961") -> str:
    """Turn whatever was typed into +<E.164>. A bare local number like
    '71234567' (or '03 123 456') gets the default country code; '+961…',
    '00961…' and '961…' are all accepted as-is."""
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


class JobCreate(BaseModel):
    task: str
    contact: str


@router.get("", include_in_schema=False)
async def control_ui():
    path = os.path.join(os.path.dirname(__file__), "static", "control.html")
    return FileResponse(path)


@router.get("/jobs")
async def list_jobs():
    return _db.list_jobs()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = _db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs", status_code=201)
async def create_job(body: JobCreate):
    if not body.task.strip():
        raise HTTPException(status_code=422, detail="Task cannot be empty")
    if not body.contact.strip():
        raise HTTPException(status_code=422, detail="Contact cannot be empty")

    cc = getattr(_cfg, "DEFAULT_COUNTRY_CODE", "961")
    contact = _normalize_contact(body.contact, cc)
    job = _db.create_job(task=body.task.strip(), contact=contact)

    scheme = "https" if "localhost" not in _cfg.DOMAIN else "http"
    call_url = f"{scheme}://{_cfg.DOMAIN}/?job={job['id']}"
    msg = (
        f"Hey! Adam asked his AI assistant to call you.\n"
        f"Tap the link to answer: {call_url}\n\n"
        f"(This is an AI, not a real person)"
    )
    sent = await _wa.send(contact, msg)

    _db.update_job(
        job["id"],
        link_sent_at=datetime.now(timezone.utc).isoformat() if sent else None,
    )
    return _db.get_job(job["id"])
