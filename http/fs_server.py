from pathlib import Path
import os
import re
import shutil
from lib.logging_utils import configure_http_access_logger, log_http_access, log_tool_call
from mcp.server.fastmcp import FastMCP

_root_env = os.environ.get("MCP_THREADS_ROOT", "/srv/ai-share")
ROOT_BASE = Path(_root_env).resolve()
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "9001"))

THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
RESERVED_THREAD_IDS = {"default", "root", "tmp", "test"}

mcp = FastMCP("filesystem", host=HOST, port=PORT)

configure_http_access_logger()


def validate_thread_id(thread_id: str) -> str:
    thread_id = thread_id.strip()
    if not thread_id:
        raise ValueError("thread_id is required")
    if thread_id in RESERVED_THREAD_IDS:
        raise ValueError(f"thread_id '{thread_id}' is not allowed")
    if thread_id in {".", ".."}:
        raise ValueError("invalid thread_id")
    if not THREAD_ID_RE.fullmatch(thread_id):
        raise ValueError(
            "invalid thread_id: must start with a letter or digit and then use "
            "letters, numbers, dot, underscore, or hyphen (3-128 chars total)"
        )
    return thread_id

def get_thread_root(thread_id: str) -> Path:
    thread_id = validate_thread_id(thread_id)
    root = (ROOT_BASE / thread_id).resolve()
    if root != ROOT_BASE and ROOT_BASE not in root.parents:
        raise ValueError("invalid thread root")
    return root

def require_existing_thread_root(thread_id: str) -> Path:
    root = get_thread_root(thread_id)
    if not root.exists():
        raise ValueError(f"thread '{thread_id}' does not exist; call create_thread first")
    if not root.is_dir():
        raise ValueError("thread root is not a directory")
    return root

def safe_path(thread_id: str, p: str) -> Path:
    root = require_existing_thread_root(thread_id)
    candidate = (root / p.lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("path outside allowed thread root")
    return candidate

@mcp.tool()
@log_tool_call("filesystem")
def list_threads() -> list[str]:
    ROOT_BASE.mkdir(parents=True, exist_ok=True)
    return sorted([p.name for p in ROOT_BASE.iterdir() if p.is_dir()])

@mcp.tool()
@log_tool_call("filesystem")
def create_thread(thread_id: str) -> str:
    root = get_thread_root(thread_id)
    if root.exists():
        if root.is_dir():
            return f"thread directory already exists: {root}"
        raise ValueError("thread path exists but is not a directory")
    root.mkdir(parents=True, exist_ok=False)
    return f"created thread directory {root}"

@mcp.tool()
@log_tool_call("filesystem")
def delete_thread(thread_id: str, recursive: bool = False) -> str:
    root = require_existing_thread_root(thread_id)
    if recursive:
        shutil.rmtree(root)
    else:
        root.rmdir()
    return f"deleted thread directory {root}"

@mcp.tool()
@log_tool_call("filesystem")
def list_allowed_directories(thread_id: str) -> list[str]:
    root = require_existing_thread_root(thread_id)
    return [str(root)]

@mcp.tool()
@log_tool_call("filesystem")
def list_directory(thread_id: str, path: str = ".") -> list[str]:
    p = safe_path(thread_id, path)
    if not p.exists():
        raise ValueError("path does not exist")
    if not p.is_dir():
        raise ValueError("not a directory")
    return sorted(x.name for x in p.iterdir())

@mcp.tool()
@log_tool_call("filesystem")
def read_file(thread_id: str, path: str) -> str:
    p = safe_path(thread_id, path)
    if not p.is_file():
        raise ValueError("not a file")
    return p.read_text(encoding="utf-8")

@mcp.tool()
@log_tool_call("filesystem")
def write_file(thread_id: str, path: str, content: str) -> str:
    p = safe_path(thread_id, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {p}"

@mcp.tool()
@log_tool_call("filesystem")
def create_directory(thread_id: str, path: str) -> str:
    p = safe_path(thread_id, path)
    p.mkdir(parents=True, exist_ok=True)
    return f"created {p}"

@mcp.tool()
@log_tool_call("filesystem")
def delete_path(thread_id: str, path: str, recursive: bool = False) -> str:
    p = safe_path(thread_id, path)
    if not p.exists():
        raise ValueError("path does not exist")
    if p.is_dir():
        if recursive:
            shutil.rmtree(p)
        else:
            p.rmdir()
    else:
        p.unlink()
    return f"deleted {p}"

if __name__ == "__main__":
    ROOT_BASE.mkdir(parents=True, exist_ok=True)
    log_http_access("mcp server starting")
    mcp.run(transport="streamable-http")
