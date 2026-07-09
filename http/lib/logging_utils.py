from __future__ import annotations

from functools import wraps
from pathlib import Path
import json
import logging
import os
from typing import Any, Callable


LOG_DIR = Path(os.environ.get("MCP_LOG_DIR", "/opt/mcp/logs"))
LOG_FORMAT = "%(asctime)s %(levelname)s connector=%(connector)s tool=%(tool)s params=%(params)s message=%(message)s"


class MCPContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "connector"):
            record.connector = "unknown"
        if not hasattr(record, "tool"):
            record.tool = "-"
        if not hasattr(record, "params"):
            record.params = "{}"
        return True


def _ensure_handler(logger: logging.Logger, connector: str) -> None:
    log_dir = LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{connector}.log"
    target = str(log_path.resolve())
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and getattr(handler, 'baseFilename', None) == target:
            return
    handler = logging.FileHandler(log_path)
    handler.setLevel(getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO))
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(MCPContextFilter())
    logger.addHandler(handler)


def get_connector_logger(connector: str) -> logging.Logger:
    logger = logging.getLogger(f"mcp.{connector}")
    logger.setLevel(getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO))
    logger.propagate = False
    _ensure_handler(logger, connector)
    return logger


def _serialize_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > 500:
            return f"<str len={len(value)}>"
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _serialize_value(v) for k, v in value.items()}
    return value


def safe_params(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    names = fn.__code__.co_varnames[:fn.__code__.co_argcount]
    params = dict(zip(names, args))
    params.update(kwargs)
    redacted = {}
    for key, value in params.items():
        if key.lower() in {"content", "token", "password", "secret"}:
            if isinstance(value, str):
                redacted[key] = f"<redacted len={len(value)}>"
            else:
                redacted[key] = "<redacted>"
        else:
            redacted[key] = _serialize_value(value)
    return json.dumps(redacted, ensure_ascii=False, default=str)


def log_tool_call(connector: str):
    logger = get_connector_logger(connector)

    def decorator(fn: Callable[..., Any]):
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any):
            params = safe_params(fn, args, kwargs)
            logger.info(
                "mcp_request",
                extra={"connector": connector, "tool": fn.__name__, "params": params},
            )
            try:
                return fn(*args, **kwargs)
            except Exception:
                logger.exception(
                    "mcp_request_failed",
                    extra={"connector": connector, "tool": fn.__name__, "params": params},
                )
                raise

        return wrapper

    return decorator
