"""
Structured logging.

Emits one JSON object per log line so a log aggregator (CloudWatch, Loki,
Datadog, etc.) can parse fields without regex. Plain-text logging is used in
development for readability; JSON in production. The format is chosen by
`LOG_FORMAT` (env), defaulting to json.

Deliberately minimal and dependency-free (stdlib logging only). It never logs
secrets: the API key is not part of any log record, and request/DB URLs are
redacted of credentials before being logged (see `redact_url`).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Include any structured extras attached via logger.info(..., extra={...}).
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            if key in payload:
                continue
            try:
                json.dumps(value)  # only include JSON-serializable extras
                payload[key] = value
            except (TypeError, ValueError):
                payload[key] = repr(value)
        if record.exc_info:
            # Class + message only — never the full traceback, which could
            # carry host paths. Full tracebacks stay out of structured logs.
            exc_type = record.exc_info[0]
            payload["error_type"] = exc_type.__name__ if exc_type else "Error"
        return json.dumps(payload, default=str)


# LogRecord attributes we don't want to duplicate into the JSON body.
_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
}


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install a root handler. Idempotent — safe to call at startup in both
    the API and the worker."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    # Remove existing handlers so repeated calls (e.g. uvicorn reload) don't
    # stack duplicate output.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if fmt.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root.addHandler(handler)

    # Align uvicorn's own loggers with our handler so access/error logs are
    # structured too, without duplicate lines.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True


def redact_url(url: str) -> str:
    """Return a connection URL with any username/password removed, safe to
    log. e.g. postgresql://user:pw@host/db -> postgresql://host/db."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    if parts.hostname is None:
        return url  # not a URL with a netloc; nothing to redact
    netloc = parts.hostname
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
