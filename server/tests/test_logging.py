"""Tests for structured JSON logging."""

from __future__ import annotations

import json
import logging

from src.common.logging import JSONFormatter, SensitiveFieldFilter


class TestJSONFormatter:
    def test_basic_record_is_valid_json(self):
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert data["msg"] == "hello world"
        assert "ts" in data

    def test_structured_extras_included(self):
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="request",
            args=(),
            exc_info=None,
        )
        record.request_id = "abc-123"
        record.status_code = 200
        output = fmt.format(record)
        data = json.loads(output)
        assert data["request_id"] == "abc-123"
        assert data["status_code"] == 200

    def test_exception_traceback_included(self):
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="error occurred",
            args=(),
            exc_info=exc_info,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError" in data["exception"]


class TestSensitiveFieldRedaction:
    """T-M5-02d: sensitive field values must be replaced with ***."""

    def _make_record(self, **extra_fields):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="sensitive test",
            args=(),
            exc_info=None,
        )
        for key, value in extra_fields.items():
            setattr(record, key, value)
        return record

    def test_token_is_redacted(self):
        fmt = JSONFormatter()
        record = self._make_record(token="sk-abc123secret")
        output = fmt.format(record)
        # The extras whitelist doesn't include 'token', so it won't appear
        # in the JSON output at all.  But if someone adds it to the whitelist,
        # it must be redacted.
        assert "sk-abc123secret" not in output

    def test_sensitive_key_in_whitelist_is_redacted(self):
        """SensitiveFieldFilter redacts any attribute matching the pattern."""
        filt = SensitiveFieldFilter()
        record = self._make_record(password="hunter2", secret_key="sk-live-abc")
        assert filt.filter(record) is True
        assert record.password == "***"
        assert record.secret_key == "***"

    def test_non_sensitive_fields_not_redacted(self):
        filt = SensitiveFieldFilter()
        record = self._make_record(request_id="abc-123", user_id="u-42")
        filt.filter(record)
        assert record.request_id == "abc-123"
        assert record.user_id == "u-42"

    def test_refresh_token_is_redacted(self):
        filt = SensitiveFieldFilter()
        record = self._make_record(refresh_token="long-hex-token")
        filt.filter(record)
        assert record.refresh_token == "***"

    def test_api_key_is_redacted(self):
        filt = SensitiveFieldFilter()
        record = self._make_record(api_key="sk-proj-xyz")
        filt.filter(record)
        assert record.api_key == "***"

    def test_x_api_key_header_form_is_redacted(self):
        """HTTP header form ``x_api_key`` (LogRecord attr) must also redact."""
        filt = SensitiveFieldFilter()
        record = self._make_record(x_api_key="header-secret")
        filt.filter(record)
        assert record.x_api_key == "***"

    def test_authorization_is_redacted(self):
        filt = SensitiveFieldFilter()
        record = self._make_record(authorization="Bearer eyJhbGci...")
        filt.filter(record)
        assert record.authorization == "***"

    def test_auth_token_prefixed_is_redacted(self):
        """Prefixed token attr ``auth_token`` must redact."""
        filt = SensitiveFieldFilter()
        record = self._make_record(auth_token="bearer-xyz")
        filt.filter(record)
        assert record.auth_token == "***"

    def test_prompt_tokens_not_redacted(self):
        """``prompt_tokens`` is NOT a sensitive field (plural form)."""
        filt = SensitiveFieldFilter()
        record = self._make_record(prompt_tokens=500, completion_tokens=200)
        filt.filter(record)
        assert record.prompt_tokens == 500
        assert record.completion_tokens == 200
