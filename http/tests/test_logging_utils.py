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

from lib.logging_utils import configure_http_access_logger, get_connector_logger, log_http_access, log_http_access_request, safe_params


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


def test_get_connector_logger_writes_to_connector_log_file():
    logger = get_connector_logger("filesystem")
    logger.info(
        "mcp_request",
        extra={"connector": "filesystem", "tool": "read_file", "params": '{"path":"a.txt"}'},
    )
    for handler in logger.handlers:
        handler.flush()
    log_path = Path(_tmpdir.name) / "filesystem.log"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "connector=filesystem" in content
    assert "tool=read_file" in content
    assert 'params={"path":"a.txt"}' in content


def test_configure_http_access_logger_writes_access_log_file():
    logger = configure_http_access_logger()
    logger.info('127.0.0.1:12345 - "POST /mcp HTTP/1.1" 200')
    for handler in logger.handlers:
        handler.flush()
    log_path = Path(_tmpdir.name) / 'http-access.log'
    assert log_path.exists()
    content = log_path.read_text(encoding='utf-8')
    assert 'POST /mcp HTTP/1.1' in content



def test_log_http_access_writes_message():
    log_http_access('request received')
    access_path = Path(_tmpdir.name) / 'http-access.log'
    assert access_path.exists()
    content = access_path.read_text(encoding='utf-8')
    assert 'request received' in content
    assert content.strip() != ''


def test_log_http_access_request_writes_tool_entry():
    log_http_access_request('read_file', '{"path":"a.txt"}')
    access_path = Path(_tmpdir.name) / 'http-access.log'
    content = access_path.read_text(encoding='utf-8')
    assert 'tool=read_file' in content
    assert 'params={"path":"a.txt"}' in content

if __name__ == '__main__':
    test_safe_params_redacts_content_and_serializes_values()
    test_get_connector_logger_writes_to_connector_log_file()
    test_configure_http_access_logger_writes_access_log_file()
    test_log_http_access_writes_message()
    test_log_http_access_request_writes_tool_entry()
    print('ok')
