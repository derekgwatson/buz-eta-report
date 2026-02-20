"""Tests for API report generation and download endpoints."""
import json


class _FakeRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def test_generate_report_returns_job_id(client, monkeypatch, api_headers):
    customer_row = _FakeRow(id=1)
    monkeypatch.setattr(
        "api.reports.query_db",
        lambda *a, **kw: customer_row if kw.get("one") else [customer_row],
        raising=True,
    )
    monkeypatch.setattr(
        "api.reports.create_job", lambda jid: None, raising=True
    )

    class FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            pass  # don't actually run background job

    monkeypatch.setattr(client.application, "executor", FakeExecutor())

    r = client.post(
        "/api/v1/reports/abc123/generate", headers=api_headers
    )
    assert r.status_code == 202
    data = r.get_json()["data"]
    assert "job_id" in data
    assert len(data["job_id"]) > 0


def test_generate_report_customer_not_found(client, monkeypatch, api_headers):
    monkeypatch.setattr(
        "api.reports.query_db", lambda *a, **kw: None, raising=True
    )
    r = client.post(
        "/api/v1/reports/nonexistent/generate", headers=api_headers
    )
    assert r.status_code == 404


def test_download_csv(client, monkeypatch, api_headers):
    sample_rows = [
        {"RefNo": "ORD-1", "ProductionStatus": "Open", "ProductionLine": "Cutting"},
    ]
    monkeypatch.setattr(
        "api.reports.fetch_report_rows_and_name",
        lambda *a, **kw: (sample_rows, "Acme Corp"),
        raising=True,
    )
    r = client.get(
        "/api/v1/reports/abc123/download?format=csv", headers=api_headers
    )
    assert r.status_code == 200
    assert r.content_type == "text/csv; charset=utf-8"
    assert b"RefNo" in r.data


def test_download_xlsx(client, monkeypatch, api_headers):
    sample_rows = [
        {"RefNo": "ORD-1", "ProductionStatus": "Open"},
    ]
    monkeypatch.setattr(
        "api.reports.fetch_report_rows_and_name",
        lambda *a, **kw: (sample_rows, "Acme Corp"),
        raising=True,
    )
    r = client.get(
        "/api/v1/reports/abc123/download?format=xlsx", headers=api_headers
    )
    assert r.status_code == 200
    assert "spreadsheetml" in r.content_type


def test_download_invalid_format(client, monkeypatch, api_headers):
    monkeypatch.setattr(
        "api.reports.fetch_report_rows_and_name",
        lambda *a, **kw: ([], "Acme"),
        raising=True,
    )
    r = client.get(
        "/api/v1/reports/abc123/download?format=pdf", headers=api_headers
    )
    assert r.status_code == 400
    assert r.get_json()["code"] == "BAD_REQUEST"


def test_download_customer_not_found(client, monkeypatch, api_headers):
    monkeypatch.setattr(
        "api.reports.fetch_report_rows_and_name",
        lambda *a, **kw: (None, None),
        raising=True,
    )
    r = client.get(
        "/api/v1/reports/nonexistent/download?format=csv", headers=api_headers
    )
    assert r.status_code == 404


def test_download_with_filters(client, monkeypatch, api_headers):
    sample_rows = [
        {"RefNo": "ORD-1", "ProductionStatus": "Open", "ProductionLine": "Cutting", "Instance": "DD"},
        {"RefNo": "ORD-2", "ProductionStatus": "Closed", "ProductionLine": "Assembly", "Instance": "CBR"},
    ]
    filter_args = {}

    original_apply = None

    def capture_filters(rows, *, status=None, group=None, supplier=None):
        filter_args["status"] = status
        filter_args["group"] = group
        filter_args["supplier"] = supplier
        return rows

    monkeypatch.setattr(
        "api.reports.fetch_report_rows_and_name",
        lambda *a, **kw: (sample_rows, "Acme"),
        raising=True,
    )
    monkeypatch.setattr("api.reports.apply_filters", capture_filters, raising=True)

    r = client.get(
        "/api/v1/reports/abc123/download?format=csv&status=Open&group=Cutting&supplier=DD",
        headers=api_headers,
    )
    assert r.status_code == 200
    assert filter_args["status"] == "Open"
    assert filter_args["group"] == "Cutting"
    assert filter_args["supplier"] == "DD"


def test_download_default_format_is_csv(client, monkeypatch, api_headers):
    """When no format param is provided, default to CSV."""
    sample_rows = [{"RefNo": "ORD-1"}]
    monkeypatch.setattr(
        "api.reports.fetch_report_rows_and_name",
        lambda *a, **kw: (sample_rows, "Acme"),
        raising=True,
    )
    r = client.get(
        "/api/v1/reports/abc123/download", headers=api_headers
    )
    assert r.status_code == 200
    assert r.content_type == "text/csv; charset=utf-8"
