import os
import tempfile
import pytest
from db import Database


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    d = Database(path)
    yield d
    d.close()
    os.unlink(path)


def test_create_job_returns_dict_with_waiting_status(db):
    job = db.create_job(task="ask how their day went", contact="+15550142")
    assert job["status"] == "waiting"
    assert job["task"] == "ask how their day went"
    assert job["contact"] == "+15550142"
    assert job["id"]
    assert job["transcript"] is None


def test_get_job_returns_same_row(db):
    job = db.create_job(task="test", contact="+1")
    fetched = db.get_job(job["id"])
    assert fetched["id"] == job["id"]


def test_get_job_returns_none_for_unknown_id(db):
    assert db.get_job("nonexistent") is None


def test_update_job_status(db):
    job = db.create_job(task="test", contact="+1")
    db.update_job(job["id"], status="live")
    assert db.get_job(job["id"])["status"] == "live"


def test_update_job_transcript(db):
    job = db.create_job(task="test", contact="+1")
    db.update_job(job["id"], transcript="AI: Hi\nThem: Hello")
    assert db.get_job(job["id"])["transcript"] == "AI: Hi\nThem: Hello"


def test_list_jobs_newest_first(db):
    db.create_job(task="first", contact="+1")
    db.create_job(task="second", contact="+2")
    jobs = db.list_jobs()
    assert len(jobs) == 2
    assert jobs[0]["task"] == "second"


def test_list_jobs_empty(db):
    assert db.list_jobs() == []
