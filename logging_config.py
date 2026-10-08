"""
BankForge — centralized logging.

Required by §5 of the capstone brief:
  - Every traced function logs an ENTER line (DEBUG) with the function's
    fully qualified name, a call_id, and its bound arguments.
  - An EXIT line (DEBUG) with the same call_id, a duration_ms, and a
    truncated preview of the return value.
  - On exception, a FAILED line (ERROR) with the same call_id,
    duration_ms, exception type, and full traceback — then the exception
    is re-raised (tracing must never swallow an error).
  - All log lines are structured JSON, one object per line.
  - LOG_LEVEL defaults to DEBUG everywhere via an environment variable.

Usage:
    from logging_config import get_logger, trace

    logger = get_logger(__name__)

    @trace(logger)
    def get_account(account_id: str) -> dict:
        ...

Apply @trace to every Tool, Resource, and Prompt function — §5's warning
box is explicit that inconsistent coverage counts as incomplete
observability, not a minor gap. Every tool/resource/prompt function in
this project is decorated.
"""
from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import re
import sys
import time
import uuid
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Level configuration — DEBUG by default, everywhere, unless overridden.
# ---------------------------------------------------------------------------

DEFAULT_LOG_LEVEL = "DEBUG"
LOG_LEVEL = os.environ.get("LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()


class JsonFormatter(logging.Formatter):
    """One JSON object per log line: timestamp, level, logger name,
    service name (via SERVICE_NAME env var), message, and any extra
    fields attached via extra={"extra_fields": {...}}.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "service": getattr(record, "service", os.environ.get("SERVICE_NAME", "bankforge")),
            "message": record.getMessage(),
        }
        extra_fields = getattr(record, "extra_fields", None)
        if extra_fields:
            payload["fields"] = extra_fields
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_CONFIGURED_LOGGERS: set[str] = set()


def get_logger(name: str) -> logging.Logger:
    """Returns a logger configured with the JSON formatter and DEBUG-by-default
    level. Safe to call repeatedly (e.g. once per module) — configures the
    underlying handler only once per logger name.
    """
    logger = logging.getLogger(name)

    if name not in _CONFIGURED_LOGGERS:
        # stderr, not stdout: stdio-transport MCP servers use stdout
        # exclusively for the JSON-RPC protocol stream. A DEBUG-level
        # logger writing to stdout would interleave log lines with
        # protocol messages and corrupt the stream the moment a server
        # runs under `mcp.run(transport="stdio")` — this matters even
        # though it's invisible when exercising logic.py directly (via
        # run_local_demo.py or pytest), since neither of those goes
        # through the stdio transport at all.
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(LOG_LEVEL)
        logger.propagate = False
        _CONFIGURED_LOGGERS.add(name)

    return logger


def set_global_log_level(level: str) -> None:
    """Override the level on every logger configured so far. Used by tests
    that want to temporarily quiet or loosen logging."""
    resolved = level.upper()
    for name in _CONFIGURED_LOGGERS:
        logging.getLogger(name).setLevel(resolved)


# ---------------------------------------------------------------------------
# @trace — ENTER / EXIT / FAILED logging for any function, sync or async.
# ---------------------------------------------------------------------------

def _safe_repr(value: Any, max_len: int = 200) -> Any:
    """Best-effort, log-safe representation of a value. Falls back to a
    truncated str() for anything that isn't already JSON-safe, so a call
    with an unexpected argument type never crashes logging itself."""
    try:
        json.dumps(value)
        text = value
    except TypeError:
        text = str(value)
    if isinstance(text, str) and len(text) > max_len:
        return text[:max_len] + f"...<truncated, {len(text)} chars total>"
    return text


# PII-shaped key names a result value might carry at any nesting depth —
# intentionally duplicated from guardrails._PII_KEY_PATTERNS rather than
# imported, since guardrails.py imports get_logger/trace from this module;
# importing guardrails back from here would create a circular import.
# Both lists must be kept in sync if either changes.
_RESULT_PII_KEY_PATTERN = re.compile(
    r"(account_id|account_number|customer_id|phone|email|pan|aadhaar|card_number)",
    re.IGNORECASE,
)


def _redact_result_preview(value: Any) -> Any:
    """Recursively masks PII-shaped dict keys anywhere inside a return
    value before it becomes an EXIT line's result_preview. This is
    separate from guardrails.redact_for_logging (which only ever sees a
    flat {arg_name: value} mapping for ENTER lines) because Tool/Resource
    return values commonly nest PII-shaped fields several levels deep —
    e.g. {"accounts": [{"account_id": ..., "customer_id": ...}, ...]} —
    and applied unconditionally (not just when a `redact=` was supplied),
    so a function that never opted into ENTER-arg redaction still can't
    leak a raw database row through its EXIT line.
    """
    if isinstance(value, dict):
        redacted = {}
        for k, v in value.items():
            if _RESULT_PII_KEY_PATTERN.search(k):
                redacted[k] = _mask_scalar_for_log(v) if not isinstance(v, (dict, list)) else _redact_result_preview(v)
            else:
                redacted[k] = _redact_result_preview(v)
        return redacted
    if isinstance(value, list):
        return [_redact_result_preview(item) for item in value]
    return value


def _mask_scalar_for_log(value: Any) -> str:
    """Same masking scheme as guardrails._mask_log_value, duplicated here
    for the same circular-import reason as _RESULT_PII_KEY_PATTERN above."""
    text = str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return text[:2] + "*" * (len(text) - 4) + text[-2:]


def _bind_args(func: Callable, args: tuple, kwargs: dict) -> dict:
    """Maps positional + keyword arguments back onto parameter names, so
    ENTER logs read as {"account_id": "ACC-10001"} instead of a bare tuple."""
    try:
        sig = inspect.signature(func)
        bound = sig.bind_partial(*args, **kwargs)
        return {k: _safe_repr(v) for k, v in bound.arguments.items() if k != "self"}
    except TypeError:
        return {"args": _safe_repr(args), "kwargs": _safe_repr(kwargs)}


def trace(logger: logging.Logger, redact: Callable[[dict], dict] | None = None):
    """Decorator factory. Logs ENTER before the call and EXIT (or FAILED)
    after, at DEBUG level, including a call_id that ties the two lines
    together and a duration in milliseconds.

    `redact`, if given, is applied to the bound-arguments dict before it's
    logged — pass guardrails.redact_for_logging to keep PII out of logs
    without losing the shape of what was called.
    """

    def decorator(func: Callable) -> Callable:
        is_async = inspect.iscoroutinefunction(func)
        qualname = f"{func.__module__}.{func.__qualname__}"

        def _log_enter(call_id: str, bound_args: dict) -> None:
            if redact:
                bound_args = redact(bound_args)
            logger.debug(
                f"ENTER {qualname}",
                extra={"extra_fields": {"event": "enter", "call_id": call_id, "function": qualname, "args": bound_args}},
            )

        def _log_exit(call_id: str, start: float, result: Any) -> None:
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            safe_result = _redact_result_preview(result)
            logger.debug(
                f"EXIT {qualname}",
                extra={"extra_fields": {
                    "event": "exit", "call_id": call_id, "function": qualname,
                    "duration_ms": duration_ms, "result_preview": _safe_repr(safe_result),
                }},
            )

        def _log_failure(call_id: str, start: float, exc: Exception) -> None:
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            logger.error(
                f"FAILED {qualname}: {exc}",
                extra={"extra_fields": {
                    "event": "failed", "call_id": call_id, "function": qualname,
                    "duration_ms": duration_ms, "error_type": type(exc).__name__,
                }},
                exc_info=True,
            )

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                call_id = uuid.uuid4().hex[:12]
                bound_args = _bind_args(func, args, kwargs)
                _log_enter(call_id, bound_args)
                start = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    _log_failure(call_id, start, exc)
                    raise
                _log_exit(call_id, start, result)
                return result
            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            call_id = uuid.uuid4().hex[:12]
            bound_args = _bind_args(func, args, kwargs)
            _log_enter(call_id, bound_args)
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                _log_failure(call_id, start, exc)
                raise
            _log_exit(call_id, start, result)
            return result
        return sync_wrapper

    return decorator


if __name__ == "__main__":
    logger = get_logger("logging_config.selftest")

    @trace(logger)
    def add(a: int, b: int) -> int:
        return a + b

    @trace(logger)
    def will_fail():
        raise ValueError("intentional failure for the self-test")

    print(f"LOG_LEVEL={LOG_LEVEL}")
    add(2, 3)
    try:
        will_fail()
    except ValueError:
        pass
