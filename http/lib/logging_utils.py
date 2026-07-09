from __future__ import annotations

from functools import wraps
from pathlib import Path
import json
import logging
import os
from typing import Any, Callable


LOG_DIR = Path(os.environ.get("MCP_LOG_DIR", "/var/log/mcp"))
LOG_FORMAT = "%(asctime)s %(levelname)s connector=%(connector)s tool=%(tool)s params=%(params)s message=%(message)s"
HTTP_ACCESS_LOG_NAME = os.environ.get("MCP_HTTP_ACCESS_LOG_NAME", "http-access")


class MCPContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "connector"):
            record.connector = "unknown"
        if not hasattr(record, "tool"):
            record.tool = "-"
        if not hasattr(record, "params"):
            record.params = "{}"
        return True


def _ensure_log_dir() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR


def _ensure_handler(logger: logging.Logger, log_name: str, formatter: logging.Formatter, level: int, add_context_filter: bool = True) -> None:
    log_dir = _ensure_log_dir()
    log_path = log_dir / f"{log_name}.log"
    target = str(log_path.resolve())
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", None) == target:
            return
    handler = logging.FileHandler(log_path)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    if add_context_filter:
        handler.addFilter(MCPContextFilter())
    logger.addHandler(handler)


def get_connector_logger(connector: str) -> logging.Logger:
    level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logger = logging.getLogger(f"mcp.{connector}")
    logger.setLevel(level)
    logger.propagate = False
    _ensure_handler(logger, connector, logging.Formatter(LOG_FORMAT), level)
    return logger


def configure_http_access_logger() -> logging.Logger:
    level = getattr(logging, os.environ.get("HTTP_ACCESS_LOG_LEVEL", os.environ.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    logger = logging.getLogger("uvicorn.access")
    logger.setLevel(level)
    logger.propagate = False
    logger.handlers = []
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    _ensure_handler(logger, HTTP_ACCESS_LOG_NAME, formatter, level, add_context_filter=False)
    access_path = LOG_DIR / f"{HTTP_ACCESS_LOG_NAME}.log"
    access_path.touch(exist_ok=True)
    logger.info("access logger ready")
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


def log_http_access(message: str) -> None:
    logger = logging.getLogger("mcp.http_access")
    logger.info(message)


def configure_http_access_logger() -> logging.Logger:
    level = getattr(logging, os.environ.get("HTTP_ACCESS_LOG_LEVEL", os.environ.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    logger = logging.getLogger("mcp.http_access")
    logger.setLevel(level)
    logger.propagate = False
    logger.handlers = []
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    _ensure_handler(logger, HTTP_ACCESS_LOG_NAME, formatter, level, add_context_filter=False)
    return logger


def configure_http_access_logger() -> logging.Logger:
    level = getattr(logging, os.environ.get("HTTP_ACCESS_LOG_LEVEL", os.environ.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    logger = logging.getLogger("mcp.http_access")
    logger.setLevel(level)
    logger.propagate = False
    logger.handlers = []
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    _ensure_handler(logger, HTTP_ACCESS_LOG_NAME, formatter, level, add_context_filter=False)
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


def log_http_access(message: str) -> None:
    logger = logging.getLogger("mcp.http_access")
    logger.info(message)
