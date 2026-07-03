import os
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
    client.post("/control/jobs", json={"task": "test task", "contact": "+1"})
    r = client.get("/control/jobs")
    assert len(r.json()) == 1


def test_get_job_by_id(app_client):
    client, _ = app_client
    created = client.post("/control/jobs", json={"task": "test", "contact": "+1"}).json()
    r = client.get(f"/control/jobs/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_unknown_job_returns_404(app_client):
    client, _ = app_client
    r = client.get("/control/jobs/nonexistent")
    assert r.status_code == 404


def test_create_job_empty_task_returns_422(app_client):
    client, _ = app_client
    r = client.post("/control/jobs", json={"task": "", "contact": "+1"})
    assert r.status_code == 422


def test_create_job_empty_contact_returns_422(app_client):
    client, _ = app_client
    r = client.post("/control/jobs", json={"task": "test", "contact": ""})
    assert r.status_code == 422
