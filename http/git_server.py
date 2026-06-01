from pathlib import Path
import os
import re
import subprocess
from mcp.server.fastmcp import FastMCP

_root_env = os.environ.get("MCP_THREADS_ROOT", "/srv/ai-share")
ROOT_BASE = Path(_root_env).resolve()
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "9002"))
DEFAULT_BRANCH = os.environ.get("GIT_DEFAULT_BRANCH", "main")
GIT_USER_NAME = os.environ.get("GIT_USER_NAME", "MCP Bot")
GIT_USER_EMAIL = os.environ.get("GIT_USER_EMAIL", "mcp-bot@example.net")

THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
RESERVED_THREAD_IDS = {"default", "root", "tmp", "test"}

mcp = FastMCP("git", host=HOST, port=PORT)

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

def get_thread_repo(thread_id: str) -> Path:
    thread_id = validate_thread_id(thread_id)
    repo = (ROOT_BASE / thread_id).resolve()
    if repo != ROOT_BASE and ROOT_BASE not in repo.parents:
        raise ValueError("invalid thread repo")
    return repo

def require_existing_thread_repo_dir(thread_id: str) -> Path:
    repo = get_thread_repo(thread_id)
    if not repo.exists():
        raise ValueError(
            f"thread '{thread_id}' does not exist; call create_thread on the filesystem server first"
        )
    if not repo.is_dir():
        raise ValueError("thread repo path is not a directory")
    return repo

def ensure_git_repo(repo: Path) -> None:
    if not (repo / ".git").is_dir():
        raise ValueError(
            "git repo not initialized for this thread; call git_init_thread_repo or git_clone first"
        )

def run_git(repo: Path, args: list[str]) -> str:
    ensure_git_repo(repo)
    cmd = ["git", "-C", str(repo)] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()

def set_git_identity(repo: Path) -> None:
    for key, value in (
        ("user.name", GIT_USER_NAME),
        ("user.email", GIT_USER_EMAIL),
    ):
        result = subprocess.run(
            ["git", "-C", str(repo), "config", key, value],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise ValueError(result.stderr.strip() or f"git config {key} failed")

@mcp.tool()
def list_threads() -> list[str]:
    ROOT_BASE.mkdir(parents=True, exist_ok=True)
    return sorted([p.name for p in ROOT_BASE.iterdir() if p.is_dir()])

@mcp.tool()
def git_init_thread_repo(thread_id: str) -> str:
    repo = require_existing_thread_repo_dir(thread_id)
    if not (repo / ".git").is_dir():
        result = subprocess.run(
            ["git", "-C", str(repo), "init", "-b", DEFAULT_BRANCH],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise ValueError(result.stderr.strip() or "git init failed")
    set_git_identity(repo)
    return f"initialized git repo in {repo}"

@mcp.tool()
def git_clone(thread_id: str, url: str) -> str:
    repo = get_thread_repo(thread_id)
    if not repo.exists():
        raise ValueError(
            f"thread '{thread_id}' does not exist; call create_thread on the filesystem server first"
        )
    if not repo.is_dir():
        raise ValueError("thread repo path is not a directory")
    if (repo / ".git").is_dir():
        raise ValueError(
            f"thread '{thread_id}' already contains a git repo; use git_pull to update it"
        )
    result = subprocess.run(
        ["git", "clone", "--", url, str(repo)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "git clone failed")
    set_git_identity(repo)
    return f"cloned {url} into {repo}"

@mcp.tool()
def git_status(thread_id: str) -> str:
    return run_git(require_existing_thread_repo_dir(thread_id), ["status", "--short", "--branch"])

@mcp.tool()
def git_log(thread_id: str, max_count: int = 10) -> str:
    return run_git(
        require_existing_thread_repo_dir(thread_id),
        ["log", f"--max-count={max_count}", "--oneline", "--decorate"],
    )

@mcp.tool()
def git_diff(thread_id: str, ref: str = "HEAD") -> str:
    return run_git(require_existing_thread_repo_dir(thread_id), ["diff", ref])

@mcp.tool()
def git_add(thread_id: str, pathspec: str = ".") -> str:
    return run_git(require_existing_thread_repo_dir(thread_id), ["add", "--", pathspec])

@mcp.tool()
def git_commit(thread_id: str, message: str) -> str:
    return run_git(require_existing_thread_repo_dir(thread_id), ["commit", "-m", message])

@mcp.tool()
def git_branch_list(thread_id: str) -> str:
    return run_git(require_existing_thread_repo_dir(thread_id), ["branch", "-vv"])

@mcp.tool()
def git_checkout(thread_id: str, branch: str) -> str:
    return run_git(require_existing_thread_repo_dir(thread_id), ["checkout", branch])

@mcp.tool()
def git_pull(thread_id: str, remote: str = "origin", branch: str = DEFAULT_BRANCH) -> str:
    return run_git(require_existing_thread_repo_dir(thread_id), ["pull", remote, branch])

@mcp.tool()
def git_push(thread_id: str, remote: str = "origin", branch: str = DEFAULT_BRANCH) -> str:
    return run_git(require_existing_thread_repo_dir(thread_id), ["push", remote, branch])

if __name__ == "__main__":
    ROOT_BASE.mkdir(parents=True, exist_ok=True)
    mcp.run(transport="streamable-http")
