"""
Tests for logging_config.py — ENTER/EXIT/FAILED, call_id correlation,
structured JSON, and exception re-raising (§5).
"""
from __future__ import annotations

import io
import json
import logging

from tests import _bootstrap  # noqa: F401
from logging_config import JsonFormatter, get_logger, trace


def _capture_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    """Builds a fresh logger writing to an in-memory stream, bypassing the
    module-level _CONFIGURED_LOGGERS cache so each test gets a clean capture."""
    stream = io.StringIO()
    logger = logging.getLogger(name)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel("DEBUG")
    logger.propagate = False
    return logger, stream


def test_trace_emits_enter_and_exit_lines():
    logger, stream = _capture_logger("test.trace.enter_exit")

    @trace(logger)
    def add(a, b):
        return a + b

    add(2, 3)
    lines = [json.loads(l) for l in stream.getvalue().strip().splitlines()]
    assert len(lines) == 2
    assert lines[0]["fields"]["event"] == "enter"
    assert lines[1]["fields"]["event"] == "exit"
    assert lines[0]["fields"]["call_id"] == lines[1]["fields"]["call_id"]
    assert lines[1]["fields"]["duration_ms"] >= 0
    assert lines[1]["fields"]["result_preview"] == 5


def test_trace_emits_failed_line_and_reraises():
    logger, stream = _capture_logger("test.trace.failed")

    @trace(logger)
    def boom():
        raise ValueError("kaboom")

    try:
        boom()
        assert False, "exception should have propagated"
    except ValueError:
        pass

    lines = [json.loads(l) for l in stream.getvalue().strip().splitlines()]
    assert lines[0]["fields"]["event"] == "enter"
    assert lines[1]["fields"]["event"] == "failed"
    assert lines[1]["level"] == "ERROR"
    assert lines[1]["fields"]["error_type"] == "ValueError"
    assert "exception" in lines[1]


def test_trace_redacts_arguments_when_redact_supplied():
    logger, stream = _capture_logger("test.trace.redact")

    def redact(args):
        return {k: "***" for k in args}

    @trace(logger, redact=redact)
    def show(secret):
        return "ok"

    show("sensitive-value")
    lines = [json.loads(l) for l in stream.getvalue().strip().splitlines()]
    assert lines[0]["fields"]["args"]["secret"] == "***"


def test_log_lines_are_valid_json_one_object_per_line():
    logger, stream = _capture_logger("test.trace.json_shape")

    @trace(logger)
    def noop():
        return None

    noop()
    for line in stream.getvalue().strip().splitlines():
        obj = json.loads(line)  # raises if not valid JSON
        assert "timestamp" in obj and "level" in obj and "message" in obj


def test_default_log_level_is_debug():
    from logging_config import LOG_LEVEL
    assert LOG_LEVEL == "DEBUG"
