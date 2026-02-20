"""Tests for API job status endpoint."""
import time


def test_job_status_found(client, monkeypatch, api_headers):
    monkeypatch.setattr(
        "api.jobs.get_job",
        lambda jid: {
            "status": "completed",
            "pct": 100,
            "done": True,
            "error": None,
            "log": ["Loading customer...", "Ready"],
            "result": {"template": "report.html"},
            "updated_ts": time.time(),
        },
        raising=True,
    )
    r = client.get("/api/v1/jobs/job-123", headers=api_headers)
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["job_id"] == "job-123"
    assert data["status"] == "completed"
    assert data["done"] is True
    assert data["pct"] == 100
    assert "log" in data


def test_job_status_not_found(client, monkeypatch, api_headers):
    monkeypatch.setattr("api.jobs.get_job", lambda jid: None, raising=True)
    r = client.get("/api/v1/jobs/nonexistent", headers=api_headers)
    assert r.status_code == 404
    assert r.get_json()["code"] == "NOT_FOUND"


def test_job_stall_detection(client, monkeypatch, api_headers):
    stalled_ts = time.time() - 600  # 10 min ago, well past STALL_TTL
    updated = {"called": False}

    def fake_get_job(jid):
        if updated["called"]:
            return {
                "status": "failed",
                "pct": 5,
                "done": True,
                "error": "Report generation has stopped responding. Please try again.",
                "log": ["Loading customer..."],
                "result": None,
                "updated_ts": time.time(),
            }
        return {
            "status": "running",
            "pct": 5,
            "done": False,
            "error": None,
            "log": ["Loading customer..."],
            "result": None,
            "updated_ts": stalled_ts,
        }

    def fake_update_job(jid, **kw):
        updated["called"] = True

    monkeypatch.setattr("api.jobs.get_job", fake_get_job, raising=True)
    monkeypatch.setattr("api.jobs.update_job", fake_update_job, raising=True)

    r = client.get("/api/v1/jobs/stalled-job", headers=api_headers)
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["error"] is not None
    assert updated["called"]


def test_job_running(client, monkeypatch, api_headers):
    monkeypatch.setattr(
        "api.jobs.get_job",
        lambda jid: {
            "status": "running",
            "pct": 50,
            "done": False,
            "error": None,
            "log": ["Loading customer...", "Fetching orders..."],
            "result": None,
            "updated_ts": time.time(),
        },
        raising=True,
    )
    r = client.get("/api/v1/jobs/running-job", headers=api_headers)
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["status"] == "running"
    assert data["done"] is False
