"""Tests for _sanitize_for_excel (formula injection) and ODataClient._format_data date fallback."""
import pytest
from services.export import _sanitize_for_excel, to_csv_bytes, to_excel_bytes
from services.odata_client import ODataClient


# ---------- _sanitize_for_excel ----------

class TestSanitizeForExcel:
    def test_equals_prefix(self):
        assert _sanitize_for_excel("=SUM(A1)") == "'=SUM(A1)"

    def test_plus_prefix(self):
        assert _sanitize_for_excel("+1234") == "'+1234"

    def test_minus_prefix(self):
        assert _sanitize_for_excel("-1234") == "'-1234"

    def test_at_prefix(self):
        assert _sanitize_for_excel("@SUM") == "'@SUM"

    def test_safe_string_unchanged(self):
        assert _sanitize_for_excel("Hello World") == "Hello World"

    def test_none_becomes_empty(self):
        assert _sanitize_for_excel(None) == ""

    def test_integer_unchanged(self):
        assert _sanitize_for_excel(42) == "42"

    def test_empty_string(self):
        assert _sanitize_for_excel("") == ""

    def test_formula_in_csv_output(self):
        """Dangerous values should be prefixed in CSV output."""
        rows = [{"A": "=cmd|'/c calc'"}]
        data = to_csv_bytes(rows, ["A"])
        text = data.decode("utf-8-sig")
        # The dangerous value should be escaped with leading apostrophe
        assert "'=cmd|'/c calc'" in text


# ---------- ODataClient._format_data date fallback ----------

@pytest.fixture
def odata_client():
    """ODataClient with DD credentials (from conftest _env fixture)."""
    return ODataClient("DD")


class TestFormatDataDateParsing:
    def test_standard_format(self, odata_client):
        """Standard ISO 8601 without fractional seconds."""
        data = [{"RefNo": "R1", "DateScheduled": "2025-03-15T00:00:00Z"}]
        result = odata_client._format_data(data)
        assert result[0]["DateScheduled"] == "15 Mar 2025"
        assert result[0]["Instance"] == "DD"

    def test_fractional_seconds_fallback(self, odata_client):
        """ISO format with fractional seconds uses fromisoformat fallback."""
        data = [{"RefNo": "R1", "DateScheduled": "2025-03-15T10:30:00.000Z"}]
        result = odata_client._format_data(data)
        assert result[0]["DateScheduled"] == "15 Mar 2025"

    def test_timezone_offset_fallback(self, odata_client):
        """ISO format with timezone offset uses fromisoformat fallback."""
        data = [{"RefNo": "R1", "DateScheduled": "2025-03-15T10:30:00+10:00"}]
        result = odata_client._format_data(data)
        assert result[0]["DateScheduled"] == "15 Mar 2025"

    def test_unparseable_date_skipped(self, odata_client):
        """Completely invalid date is skipped, original value preserved."""
        data = [{"RefNo": "R1", "DateScheduled": "not-a-date"}]
        result = odata_client._format_data(data)
        # Date couldn't be parsed, so original value stays (no latest_date found)
        assert result[0]["DateScheduled"] == "not-a-date"

    def test_missing_date_preserved(self, odata_client):
        """Item without DateScheduled still gets Instance added."""
        data = [{"RefNo": "R1"}]
        result = odata_client._format_data(data)
        assert result[0]["Instance"] == "DD"
        assert "DateScheduled" not in result[0]

    def test_latest_date_wins_across_lines(self, odata_client):
        """Multiple lines for same RefNo get the latest date."""
        data = [
            {"RefNo": "R1", "DateScheduled": "2025-01-01T00:00:00Z"},
            {"RefNo": "R1", "DateScheduled": "2025-06-15T00:00:00Z"},
        ]
        result = odata_client._format_data(data)
        assert result[0]["DateScheduled"] == "15 Jun 2025"
        assert result[1]["DateScheduled"] == "15 Jun 2025"

    def test_mixed_valid_and_invalid_dates(self, odata_client):
        """One valid + one invalid date for same RefNo: valid date used."""
        data = [
            {"RefNo": "R1", "DateScheduled": "2025-03-15T00:00:00Z"},
            {"RefNo": "R1", "DateScheduled": "garbage"},
        ]
        result = odata_client._format_data(data)
        # Both lines get the valid parsed date
        assert result[0]["DateScheduled"] == "15 Mar 2025"
        assert result[1]["DateScheduled"] == "15 Mar 2025"

    def test_empty_data(self, odata_client):
        assert odata_client._format_data([]) == []
