"""Tests for structured JSON logging."""

from __future__ import annotations

import json
import logging

from src.common.logging import JSONFormatter


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
