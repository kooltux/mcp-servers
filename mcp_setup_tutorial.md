# MCP Server Setup Tutorial

> Proxmox Debian 13 LXC · Filesystem + Git MCP · HAProxy · Perplexity

---

## Overview

This tutorial documents a complete setup of two remote MCP servers accessible via Perplexity custom connectors.

**Architecture:**
- Proxmox LXC running Debian 13
- Filesystem MCP server on port `9001`
- Git MCP server on port `9002`
- HAProxy terminates HTTPS and routes `/fs` → filesystem, `/git` → git
- Bearer token authentication enforced in HAProxy backends
- Perplexity connects via remote MCP URLs over HTTPS (Streamable HTTP)
- Thread-level isolation via explicit `thread_id` parameter on every tool call
- Thread root directory configured via the `MCP_THREADS_ROOT` environment variable

---

## 1) Proxmox LXC Creation

Run on the Proxmox host (adjust template name, storage, IP and gateway as needed):

```bash
pct create 230 local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst \
  --hostname mcp-debian13 \
  --cores 2 \
  --memory 2048 \
  --swap 512 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=10.0.0.25/24,gw=10.0.0.1 \
  --features nesting=1 \
  --unprivileged 1 \
  --onboot 1 \
  --ostype debian

pct start 230
pct exec 230 -- bash
```

---

## 2) Base Packages

Inside the Debian 13 container:

```bash
apt update
apt install -y \
  ca-certificates \
  curl \
  git \
  python3 \
  python3-venv \
  python3-pip \
  vim \
  jq \
  wget
```

---

## 3) Service User and Directories

```bash
useradd --system --create-home --home-dir /var/lib/mcp --shell /usr/sbin/nologin mcp || true

mkdir -p /opt/mcp/http
mkdir -p /srv/ai-share
chown -R mcp:mcp /opt/mcp /srv/ai-share /var/lib/mcp
chmod 0750 /srv/ai-share
```

> **Note:** The default threads root is `/srv/ai-share`. This path is configurable via the `MCP_THREADS_ROOT` environment variable in the systemd service files.

---

## 4) Python MCP Environment

```bash
python3 -m venv /opt/mcp/http/venv
/opt/mcp/http/venv/bin/pip install --upgrade pip
/opt/mcp/http/venv/bin/pip install "mcp[cli]"
```

---

## 5) Filesystem MCP Server

Create `/opt/mcp/http/fs_server.py`:

```python
from pathlib import Path
import os
import re
import shutil
from mcp.server.fastmcp import FastMCP

_root_env = os.environ.get("MCP_THREADS_ROOT", "/srv/ai-share")
ROOT_BASE = Path(_root_env).resolve()
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "9001"))

THREAD_ID_RE = re.compile(r"^thread-[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
RESERVED_THREAD_IDS = {"default", "root", "tmp", "test"}

mcp = FastMCP("filesystem", host=HOST, port=PORT)


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
            "invalid thread_id: must start with 'thread-' and then use "
            "letters, numbers, dot, underscore, or hyphen"
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
        raise ValueError(
            f"thread '{thread_id}' does not exist; call create_thread first"
        )
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
def list_threads() -> list[str]:
    ROOT_BASE.mkdir(parents=True, exist_ok=True)
    return sorted([p.name for p in ROOT_BASE.iterdir() if p.is_dir()])


@mcp.tool()
def create_thread(thread_id: str) -> str:
    root = get_thread_root(thread_id)
    if root.exists():
        if root.is_dir():
            return f"thread directory already exists: {root}"
        raise ValueError("thread path exists but is not a directory")
    root.mkdir(parents=True, exist_ok=False)
    return f"created thread directory {root}"


@mcp.tool()
def delete_thread(thread_id: str, recursive: bool = False) -> str:
    root = require_existing_thread_root(thread_id)
    if recursive:
        shutil.rmtree(root)
    else:
        root.rmdir()
    return f"deleted thread directory {root}"


@mcp.tool()
def list_allowed_directories(thread_id: str) -> list[str]:
    root = require_existing_thread_root(thread_id)
    return [str(root)]


@mcp.tool()
def list_directory(thread_id: str, path: str = ".") -> list[str]:
    p = safe_path(thread_id, path)
    if not p.exists():
        raise ValueError("path does not exist")
    if not p.is_dir():
        raise ValueError("not a directory")
    return sorted(x.name for x in p.iterdir())


@mcp.tool()
def read_file(thread_id: str, path: str) -> str:
    p = safe_path(thread_id, path)
    if not p.is_file():
        raise ValueError("not a file")
    return p.read_text(encoding="utf-8")


@mcp.tool()
def write_file(thread_id: str, path: str, content: str) -> str:
    p = safe_path(thread_id, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {p}"


@mcp.tool()
def create_directory(thread_id: str, path: str) -> str:
    p = safe_path(thread_id, path)
    p.mkdir(parents=True, exist_ok=True)
    return f"created {p}"


@mcp.tool()
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
    mcp.run(transport="streamable-http")
```

---

## 6) Git MCP Server

Create `/opt/mcp/http/git_server.py`:

```python
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

THREAD_ID_RE = re.compile(r"^thread-[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
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
            "invalid thread_id: must start with 'thread-' and then use "
            "letters, numbers, dot, underscore, or hyphen"
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
```

---

## 7) systemd Services

### `/etc/systemd/system/mcp-filesystem.service`

```ini
[Unit]
Description=MCP Filesystem Server
After=network.target

[Service]
Type=simple
User=mcp
Group=mcp
WorkingDirectory=/opt/mcp/http
Environment="HOST=0.0.0.0"
Environment="PORT=9001"
Environment="MCP_THREADS_ROOT=/srv/ai-share"
ExecStart=/opt/mcp/http/venv/bin/python /opt/mcp/http/fs_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/mcp-git.service`

```ini
[Unit]
Description=MCP Git Server
After=network.target

[Service]
Type=simple
User=mcp
Group=mcp
WorkingDirectory=/opt/mcp/http
Environment="HOST=0.0.0.0"
Environment="PORT=9002"
Environment="MCP_THREADS_ROOT=/srv/ai-share"
Environment="GIT_DEFAULT_BRANCH=main"
Environment="GIT_USER_NAME=MCP Bot"
Environment="GIT_USER_EMAIL=mcp-bot@example.net"
ExecStart=/opt/mcp/http/venv/bin/python /opt/mcp/http/git_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

> **To change the threads root path**, update `MCP_THREADS_ROOT` in both service files, create the new directory, set ownership to `mcp:mcp`, then run `systemctl daemon-reload` and restart both services.

Enable and start:

```bash
systemctl daemon-reload
systemctl enable --now mcp-filesystem.service
systemctl enable --now mcp-git.service
```

Verify:

```bash
systemctl status mcp-filesystem.service --no-pager
systemctl status mcp-git.service --no-pager
ss -ltnp | grep -E '9001|9002'
```

---

## 8) HAProxy Token File

Shared token:

```bash
printf '%s\n' 'super-long-random-secret' >/etc/haproxy/mcp-api-key
chmod 600 /etc/haproxy/mcp-api-key
chown root:root /etc/haproxy/mcp-api-key
```

Optional separate tokens per service:

```bash
printf '%s\n' 'fs-secret-token'  >/etc/haproxy/mcp-fs-api-key
printf '%s\n' 'git-secret-token' >/etc/haproxy/mcp-git-api-key
chmod 600 /etc/haproxy/mcp-fs-api-key /etc/haproxy/mcp-git-api-key
chown root:root /etc/haproxy/mcp-fs-api-key /etc/haproxy/mcp-git-api-key
```

---

## 9) HAProxy Full Configuration (Frontend Routing)

> **Note for HAProxy 2.6.x:** `hdr_exists()` is not available. Use `req.hdr(...) -m found` instead.

```haproxy
global
    log /dev/log local0

defaults
    mode http
    log global
    option httplog
    timeout connect 5s
    timeout client 60s
    timeout server 60s

frontend fe_https
    bind *:443 ssl crt /etc/haproxy/certs/mcp.example.net.pem

    acl host_mcp hdr(host) -i mcp.example.net

    acl path_fs   path -i /fs
    acl path_fs2  path_beg /fs/
    acl path_git  path -i /git
    acl path_git2 path_beg /git/

    use_backend mcp_fs  if host_mcp path_fs
    use_backend mcp_fs  if host_mcp path_fs2
    use_backend mcp_git if host_mcp path_git
    use_backend mcp_git if host_mcp path_git2

    default_backend existing_default_backend

backend mcp_fs
    mode http

    acl has_auth req.hdr(Authorization) -m found
    http-request deny deny_status 401 if !has_auth

    acl auth_bearer req.hdr(Authorization) -m reg ^Bearer\ .+
    http-request deny deny_status 401 if !auth_bearer

    http-request set-var(txn.mcp_token) req.hdr(Authorization),regsub(^Bearer\ *,)
    acl token_valid var(txn.mcp_token) -m str -f /etc/haproxy/mcp-api-key
    http-request deny deny_status 401 if !token_valid

    http-request set-path /mcp
    server mcpfs 10.0.0.25:9001 check

backend mcp_git
    mode http

    acl has_auth req.hdr(Authorization) -m found
    http-request deny deny_status 401 if !has_auth

    acl auth_bearer req.hdr(Authorization) -m reg ^Bearer\ .+
    http-request deny deny_status 401 if !auth_bearer

    http-request set-var(txn.mcp_token) req.hdr(Authorization),regsub(^Bearer\ *,)
    acl token_valid var(txn.mcp_token) -m str -f /etc/haproxy/mcp-api-key
    http-request deny deny_status 401 if !token_valid

    http-request set-path /mcp
    server mcpgit 10.0.0.25:9002 check
```

---

## 10) Validate and Reload HAProxy

```bash
haproxy -c -f /etc/haproxy/haproxy.cfg
systemctl reload haproxy
```

---

## 11) Test Commands

Without auth (should return 401):

```bash
curl -i https://mcp.example.net/fs
curl -i https://mcp.example.net/git
```

With auth (should reach backend):

```bash
curl -i -H 'Authorization: Bearer super-long-random-secret' https://mcp.example.net/fs
curl -i -H 'Authorization: Bearer super-long-random-secret' https://mcp.example.net/git
```

---

## 12) Environment Variables Reference

### Common to both servers

| Variable | Default | Description |
|---|---|---|
| `MCP_THREADS_ROOT` | `/srv/ai-share` | Absolute path to the threads root directory |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `9001` (fs) / `9002` (git) | Bind port |

### Git server only

| Variable | Default | Description |
|---|---|---|
| `GIT_DEFAULT_BRANCH` | `main` | Default branch name used by `git init` and `git pull/push` |
| `GIT_USER_NAME` | `MCP Bot` | Git committer name set in each repo's local config |
| `GIT_USER_EMAIL` | `mcp-bot@example.net` | Git committer email set in each repo's local config |

---

## 13) Thread Usage

Each conversation thread must use its own `thread_id` following the pattern `thread-<name>`. The `thread_id` is passed explicitly to every tool call — there is no server-side session state.

### thread_id rules

- Must match `thread-[A-Za-z0-9][A-Za-z0-9._-]{2,127}`
- Must start with `thread-`
- Reserved names are rejected: `default`, `root`, `tmp`, `test`
- Thread folders only created by `create_thread()` — never auto-created by file tools

### Typical flow — new repo

```
# 1. Create the thread folder (filesystem server)
create_thread("thread-project-abc")

# 2. Initialize a fresh Git repo (git server)
git_init_thread_repo("thread-project-abc")

# 3. Write files (filesystem server)
write_file("thread-project-abc", "notes/todo.md", "hello world")

# 4. Commit (git server)
git_add("thread-project-abc", ".")
git_commit("thread-project-abc", "Initial commit")
```

### Typical flow — clone existing repo

```
# 1. Create the thread folder (filesystem server)
create_thread("thread-project-abc")

# 2. Clone an existing remote repo into the thread folder (git server)
git_clone("thread-project-abc", "https://github.com/example/myrepo.git")

# 3. Work with files normally (filesystem server)
read_file("thread-project-abc", "README.md")
write_file("thread-project-abc", "notes.md", "my notes")

# 4. Commit and push (git server)
git_add("thread-project-abc", ".")
git_commit("thread-project-abc", "Add notes")
git_push("thread-project-abc")
```

### Filesystem on disk

```
$MCP_THREADS_ROOT/          (default: /srv/ai-share)
└── thread-project-abc/
    ├── .git/
    └── notes/
        └── todo.md
```

---

## 14) Optional Firewall Hardening (nftables)

Restrict backend ports so only HAProxy can reach them. Replace `10.0.0.10` with your HAProxy container IP:

```bash
apt install -y nftables

cat >/etc/nftables.conf <<'NFT'
table inet filter {
  chain input {
    type filter hook input priority 0;
    policy drop;

    iif "lo" accept
    ct state established,related accept

    tcp dport 22 accept
    ip saddr 10.0.0.10 tcp dport { 9001, 9002 } accept
  }
}
NFT

systemctl enable --now nftables
nft list ruleset
```

---

## 15) Optional Git over SSH

```bash
sudo -u mcp mkdir -p /var/lib/mcp/.ssh
sudo -u mcp ssh-keygen -t ed25519 -N '' -f /var/lib/mcp/.ssh/id_ed25519
cat /var/lib/mcp/.ssh/id_ed25519.pub
# Add the public key to your Git forge, then test:
sudo -u mcp ssh -T git@github.com
```

---

## 16) Perplexity Connector Settings

| Connector  | URL                            | Auth    | Transport       |
|------------|--------------------------------|---------|-----------------|
| Filesystem | https://mcp.example.net/fs     | API Key | Streamable HTTP |
| Git        | https://mcp.example.net/git    | API Key | Streamable HTTP |

The API key value must match the token stored in `/etc/haproxy/mcp-api-key`.

> **Note:** Perplexity does not support automatic `thread_id` injection. Always pass an explicit `thread_id` matching the `thread-<name>` pattern in every tool call.

---

## 17) Convert Container to Proxmox Template

```bash
pct shutdown 230
pct template 230
```

Clone for future deployments:

```bash
pct clone 230 231 --hostname mcp-a
pct set 231 --net0 name=eth0,bridge=vmbr0,ip=10.0.0.31/24,gw=10.0.0.1
pct start 231
```

---

## Quick Reference: Setup Order

1. Create Debian 13 LXC
2. Install base packages
3. Create `mcp` user and `/srv/ai-share`
4. Install Python MCP environment
5. Create `fs_server.py` and `git_server.py`
6. Create and enable systemd services (set `MCP_THREADS_ROOT` in each)
7. Add HAProxy frontend routing and backend auth
8. Validate config with `haproxy -c`
9. Reload HAProxy and test with `curl`
10. Use `create_thread("thread-<name>")` before any file or Git operation
11. Convert the container to a Proxmox template

---

## Tool Reference

### Filesystem tools (`/fs`)

| Tool | Required params | Optional params |
|------|----------------|-----------------|
| `list_threads` | — | — |
| `create_thread` | `thread_id` | — |
| `delete_thread` | `thread_id` | `recursive` (default `false`) |
| `list_allowed_directories` | `thread_id` | — |
| `list_directory` | `thread_id` | `path` (default `.`) |
| `read_file` | `thread_id`, `path` | — |
| `write_file` | `thread_id`, `path`, `content` | — |
| `create_directory` | `thread_id`, `path` | — |
| `delete_path` | `thread_id`, `path` | `recursive` (default `false`) |

### Git tools (`/git`)

| Tool | Required params | Optional params |
|------|----------------|-----------------|
| `list_threads` | — | — |
| `git_init_thread_repo` | `thread_id` | — |
| `git_clone` | `thread_id`, `url` | — |
| `git_status` | `thread_id` | — |
| `git_log` | `thread_id` | `max_count` (default `10`) |
| `git_diff` | `thread_id` | `ref` (default `HEAD`) |
| `git_add` | `thread_id` | `pathspec` (default `.`) |
| `git_commit` | `thread_id`, `message` | — |
| `git_branch_list` | `thread_id` | — |
| `git_checkout` | `thread_id`, `branch` | — |
| `git_pull` | `thread_id` | `remote`, `branch` |
| `git_push` | `thread_id` | `remote`, `branch` |

---

*Updated: June 2026*
