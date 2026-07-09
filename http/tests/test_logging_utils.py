import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_tmpdir = tempfile.TemporaryDirectory()
os.environ["MCP_LOG_DIR"] = _tmpdir.name

from lib.logging_utils import configure_http_access_logger, get_connector_logger, safe_params


def sample_tool(thread_id, path, content, retries=0):
    return thread_id, path, content, retries


def test_safe_params_redacts_content_and_serializes_values():
    payload = safe_params(
        sample_tool,
        ("thread-123", Path("docs/file.txt"), "x" * 20),
        {"retries": 2},
    )
    data = json.loads(payload)
    assert data["thread_id"] == "thread-123"
    assert data["path"] == "docs/file.txt"
    assert data["content"] == "<redacted len=20>"
    assert data["retries"] == 2


def test_connector_logger_writes_timestamped_log_file():
    logger = get_connector_logger("filesystem")
    logger.info(
        "mcp_request",
        extra={"connector": "filesystem", "tool": "read_file", "params": '{"path":"a.txt"}'},
    )
    for handler in logger.handlers:
        handler.flush()
    log_path = Path(_tmpdir.name) / "filesystem.log"
    content = log_path.read_text(encoding="utf-8")
    assert "connector=filesystem" in content
    assert "tool=read_file" in content
    assert 'params={"path":"a.txt"}' in content
    assert content[:4].isdigit()


def test_http_access_logger_uses_separate_file_handler():
    logger = configure_http_access_logger()
    logger.info("127.0.0.1 - GET / HTTP/1.1 200")
    for handler in logger.handlers:
        handler.flush()
    access_path = Path(_tmpdir.name) / "http-access.log"
    content = access_path.read_text(encoding="utf-8")
    assert "GET / HTTP/1.1 200" in content
    assert content[:4].isdigit()


if __name__ == '__main__':
    test_safe_params_redacts_content_and_serializes_values()
    test_connector_logger_writes_timestamped_log_file()
    test_http_access_logger_uses_separate_file_handler()
    print('ok')
