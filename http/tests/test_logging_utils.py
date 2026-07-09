import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.logging_utils import safe_params


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


if __name__ == '__main__':
    test_safe_params_redacts_content_and_serializes_values()
    print('ok')
