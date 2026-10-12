"""The ETA reports read the same Buz login files as bot-team's Banji/Hugo/Owen."""
import json

import pytest

from services.odata_client import ODataClient


@pytest.fixture
def creds_dir(tmp_path, monkeypatch):
    d = tmp_path / "buz"
    d.mkdir()
    monkeypatch.setenv("BUZ_CREDENTIALS_DIR", str(d))
    return d


def _write(d, org, password, username="shared@example.com"):
    (d / f"buz_credentials_{org}.json").write_text(
        json.dumps({"username": username, "password": password}), encoding="utf-8")


@pytest.mark.parametrize("source,org", [("DD", "dd"), ("CBR", "canberra")])
def test_shared_file_wins_over_env(creds_dir, source, org):
    _write(creds_dir, org, "shared-pw")
    c = ODataClient(source)
    assert (c.username, c.password) == ("shared@example.com", "shared-pw")


def test_env_is_fallback_when_no_file(creds_dir):
    c = ODataClient("DD")
    assert (c.username, c.password) == ("test-dd-user", "test-dd-pass")


def test_unreadable_file_falls_back_to_env(creds_dir):
    (creds_dir / "buz_credentials_canberra.json").write_text("{not json", encoding="utf-8")
    c = ODataClient("CBR")
    assert (c.username, c.password) == ("test-cbr-user", "test-cbr-pass")


def test_password_change_needs_no_restart(creds_dir):
    _write(creds_dir, "dd", "old-pw")
    assert ODataClient("DD").password == "old-pw"
    _write(creds_dir, "dd", "new-pw")
    assert ODataClient("DD").password == "new-pw"
