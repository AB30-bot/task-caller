import os
from datetime import datetime, timedelta, timezone
import tempfile
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock

from db import Database
from whatsapp import WhatsAppClient
import control_app
import config


@pytest.fixture
def app_client():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = Database(db_path)
    wa = MagicMock(spec=WhatsAppClient)
    wa.send = AsyncMock(return_value=True)

    app = FastAPI()
    control_app.init(db, wa, config)
    app.include_router(control_app.router)

    client = TestClient(app)
    yield client, db

    db.close()
    os.unlink(db_path)


def test_list_jobs_empty(app_client):
    client, _ = app_client
    r = client.get("/control/jobs")
    assert r.status_code == 200
    assert r.json() == []


def test_create_job_returns_201(app_client):
    client, _ = app_client
    r = client.post("/control/jobs", json={"task": "ask how their day went", "contact": "+15550142"})
    assert r.status_code == 201
    data = r.json()
    assert data["task"] == "ask how their day went"
    assert data["status"] == "waiting"
    assert data["id"]


def test_create_job_appears_in_list(app_client):
    client, _ = app_client
    client.post("/control/jobs", json={"task": "test task", "contact": "+15550142"})
    r = client.get("/control/jobs")
    assert len(r.json()) == 1


def test_get_job_by_id(app_client):
    client, _ = app_client
    created = client.post("/control/jobs", json={"task": "test", "contact": "+15550142"}).json()
    r = client.get(f"/control/jobs/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_unknown_job_returns_404(app_client):
    client, _ = app_client
    r = client.get("/control/jobs/nonexistent")
    assert r.status_code == 404


def test_create_job_empty_task_returns_422(app_client):
    client, _ = app_client
    r = client.post("/control/jobs", json={"task": "", "contact": "+15550142"})
    assert r.status_code == 422


def test_create_job_empty_contact_returns_422(app_client):
    client, _ = app_client
    r = client.post("/control/jobs", json={"task": "test", "contact": ""})
    assert r.status_code == 422


# --- validation ---------------------------------------------------------------
def test_create_job_rejects_junk_phone_number(app_client):
    client, _ = app_client
    r = client.post("/control/jobs", json={"task": "test", "contact": "12"})
    assert r.status_code == 422


def test_create_job_rejects_overlong_task(app_client):
    client, _ = app_client
    r = client.post("/control/jobs", json={"task": "x" * 501, "contact": "+15550142"})
    assert r.status_code == 422


def test_failed_whatsapp_send_leaves_link_unsent(app_client):
    client, db = app_client
    control_app._wa.send.return_value = False
    r = client.post("/control/jobs", json={"task": "test", "contact": "+15550142"})
    assert r.status_code == 201
    assert r.json()["link_sent_at"] is None


# --- dashboard auth -----------------------------------------------------------
def test_api_open_when_no_token_configured(app_client, monkeypatch):
    client, _ = app_client
    monkeypatch.setattr(config, "CONTROL_TOKEN", "")
    assert client.get("/control/jobs").status_code == 200


def test_api_requires_token_when_configured(app_client, monkeypatch):
    client, _ = app_client
    monkeypatch.setattr(config, "CONTROL_TOKEN", "s3cret")
    assert client.get("/control/jobs").status_code == 401
    assert client.get("/control/jobs", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/control/jobs", json={"task": "t", "contact": "+15550142"}).status_code == 401


def test_api_accepts_bearer_header_and_query_token(app_client, monkeypatch):
    client, _ = app_client
    monkeypatch.setattr(config, "CONTROL_TOKEN", "s3cret")
    assert client.get("/control/jobs", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/control/jobs", headers={"X-Control-Token": "s3cret"}).status_code == 200
    assert client.get("/control/jobs?token=s3cret").status_code == 200


def test_dashboard_page_itself_is_public(app_client, monkeypatch):
    client, _ = app_client
    monkeypatch.setattr(config, "CONTROL_TOKEN", "s3cret")
    assert client.get("/control").status_code == 200  # the page prompts for the token


# --- single-use, expiring links ------------------------------------------------
def test_call_link_error_cases():
    now = datetime.now(timezone.utc)
    fresh = {"status": "waiting", "created_at": now.isoformat()}
    assert control_app.call_link_error(fresh, 24, now) is None
    assert control_app.call_link_error(None, 24, now)[0] == 404
    assert control_app.call_link_error({**fresh, "status": "done"}, 24, now)[0] == 410
    assert control_app.call_link_error({**fresh, "status": "live"}, 24, now)[0] == 410
    old = {**fresh, "created_at": (now - timedelta(hours=25)).isoformat()}
    assert control_app.call_link_error(old, 24, now) == (410, "This call link has expired.")


def test_resend_link_for_waiting_job(app_client):
    client, _ = app_client
    job = client.post("/control/jobs", json={"task": "t", "contact": "+15550142"}).json()
    control_app._wa.send.reset_mock()
    r = client.post(f"/control/jobs/{job['id']}/resend")
    assert r.status_code == 200
    control_app._wa.send.assert_awaited_once()


def test_resend_refused_once_call_happened(app_client):
    client, db = app_client
    job = client.post("/control/jobs", json={"task": "t", "contact": "+15550142"}).json()
    db.update_job(job["id"], status="done")
    assert client.post(f"/control/jobs/{job['id']}/resend").status_code == 410
    assert client.post("/control/jobs/nope/resend").status_code == 404
