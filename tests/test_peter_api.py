import pytest
from unittest.mock import patch, MagicMock
import requests


class TestPeterApi:
    """Tests for the Peter API integration service."""

    def test_is_staff_member_approved(self, monkeypatch):
        """Test successful staff verification."""
        monkeypatch.setenv("BOT_API_KEY", "test-key")
        monkeypatch.setenv("PETER_URL", "https://peter.test.com")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "approved": True,
            "name": "John Smith",
            "email": "john@watsonblinds.com.au"
        }
        mock_response.raise_for_status = MagicMock()

        with patch("services.peter_api.requests.get", return_value=mock_response) as mock_get:
            from services.peter_api import is_staff_member
            result = is_staff_member("john@watsonblinds.com.au")

            assert result["approved"] is True
            assert result["name"] == "John Smith"
            assert result["email"] == "john@watsonblinds.com.au"

            mock_get.assert_called_once()
            call_args = mock_get.call_args
            assert "john@watsonblinds.com.au" in str(call_args)
            assert call_args.kwargs["headers"]["X-API-Key"] == "test-key"

    def test_is_staff_member_not_approved(self, monkeypatch):
        """Test non-staff verification."""
        monkeypatch.setenv("BOT_API_KEY", "test-key")
        monkeypatch.setenv("PETER_URL", "https://peter.test.com")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "approved": False,
            "email": "unknown@example.com"
        }
        mock_response.raise_for_status = MagicMock()

        with patch("services.peter_api.requests.get", return_value=mock_response):
            from services.peter_api import is_staff_member
            result = is_staff_member("unknown@example.com")

            assert result["approved"] is False
            assert result["name"] is None
            assert result["email"] == "unknown@example.com"

    def test_is_staff_member_missing_api_key(self, monkeypatch):
        """Test error when API key is not configured."""
        monkeypatch.delenv("BOT_API_KEY", raising=False)

        from services.peter_api import is_staff_member
        with pytest.raises(ValueError, match="BOT_API_KEY"):
            is_staff_member("test@example.com")

    def test_check_staff_with_fallback_fail_closed(self, monkeypatch):
        """Test fail_closed behavior when Peter API is unavailable."""
        monkeypatch.setenv("BOT_API_KEY", "test-key")
        monkeypatch.setenv("PETER_URL", "https://peter.test.com")

        with patch("services.peter_api.requests.get", side_effect=requests.RequestException("Network error")):
            from services.peter_api import check_staff_with_fallback
            result = check_staff_with_fallback("test@example.com", fail_open=False)

            assert result["approved"] is False
            assert result["email"] == "test@example.com"

    def test_check_staff_with_fallback_fail_open(self, monkeypatch):
        """Test fail_open behavior when Peter API is unavailable."""
        monkeypatch.setenv("BOT_API_KEY", "test-key")
        monkeypatch.setenv("PETER_URL", "https://peter.test.com")

        with patch("services.peter_api.requests.get", side_effect=requests.RequestException("Network error")):
            from services.peter_api import check_staff_with_fallback
            result = check_staff_with_fallback("test@example.com", fail_open=True)

            assert result["approved"] is True
            assert result["email"] == "test@example.com"
