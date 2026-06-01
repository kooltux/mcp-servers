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

## 5) Deploy Server Files

The repository provides a `deploy.sh` script that copies all server files to the right locations automatically:

```bash
bash deploy.sh
```

Alternatively, copy the files manually:

```bash
# Python MCP servers
cp http/fs_server.py  /opt/mcp/http/fs_server.py
cp http/git_server.py /opt/mcp/http/git_server.py
chown mcp:mcp /opt/mcp/http/fs_server.py /opt/mcp/http/git_server.py

# systemd service units
cp services/mcp-filesystem.service /etc/systemd/system/mcp-filesystem.service
cp services/mcp-git.service        /etc/systemd/system/mcp-git.service
```

> See [`http/fs_server.py`](http/fs_server.py) and [`http/git_server.py`](http/git_server.py) for the full server source code.  
> See [`services/mcp-filesystem.service`](services/mcp-filesystem.service) and [`services/mcp-git.service`](services/mcp-git.service) for the systemd unit definitions.

---

## 6) systemd Services

Enable and start both services:

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

> **To change the threads root path**, update `MCP_THREADS_ROOT` in both service files, create the new directory, set ownership to `mcp:mcp`, then run `systemctl daemon-reload` and restart both services.

---

## 7) HAProxy Token File

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

## 8) HAProxy Full Configuration (Frontend Routing)

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

## 9) Validate and Reload HAProxy

```bash
haproxy -c -f /etc/haproxy/haproxy.cfg
systemctl reload haproxy
```

---

## 10) Test Commands

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

## 11) Environment Variables Reference

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

## 12) Thread Usage

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

## 13) Optional Firewall Hardening (nftables)

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

## 14) Optional Git over SSH

```bash
sudo -u mcp mkdir -p /var/lib/mcp/.ssh
sudo -u mcp ssh-keygen -t ed25519 -N '' -f /var/lib/mcp/.ssh/id_ed25519
cat /var/lib/mcp/.ssh/id_ed25519.pub
# Add the public key to your Git forge, then test:
sudo -u mcp ssh -T git@github.com
```

---

## 15) Perplexity Connector Settings

| Connector  | URL                            | Auth    | Transport       |
|------------|--------------------------------|---------|-----------------| 
| Filesystem | https://mcp.example.net/fs     | API Key | Streamable HTTP |
| Git        | https://mcp.example.net/git    | API Key | Streamable HTTP |

The API key value must match the token stored in `/etc/haproxy/mcp-api-key`.

> **Note:** Perplexity does not support automatic `thread_id` injection. Always pass an explicit `thread_id` matching the `thread-<name>` pattern in every tool call.

---

## 16) Convert Container to Proxmox Template

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
5. Deploy server files with `deploy.sh` (or copy manually)
6. Enable systemd services (set `MCP_THREADS_ROOT` in each if needed)
7. Add HAProxy frontend routing and backend auth
8. Validate config with `haproxy -c`
9. Reload HAProxy and test with `curl`
10. Use `create_thread("thread-<name>")` before any file or Git operation
11. Convert the container to a Proxmox template

---

## Tool Reference

### Filesystem tools (`/fs`)

> Source: [`http/fs_server.py`](http/fs_server.py)

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

> Source: [`http/git_server.py`](http/git_server.py)

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
