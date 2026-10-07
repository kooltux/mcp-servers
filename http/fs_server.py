from pathlib import Path
import os
import re
import shutil
import hashlib
import tempfile
import fnmatch
from lib.logging_utils import configure_http_access_logger, log_tool_call
from mcp.server.fastmcp import FastMCP

ROOT_BASE = Path(os.environ.get("MCP_THREADS_ROOT", "/srv/ai-share")).resolve()
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "9001"))
MAX_BYTES = 2 * 1024 * 1024
THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
RESERVED_THREAD_IDS = {"default", "root", "tmp", "test"}
mcp = FastMCP("filesystem", host=HOST, port=PORT)
configure_http_access_logger()


def validate_thread_id(thread_id: str) -> str:
    thread_id = thread_id.strip()
    if thread_id in RESERVED_THREAD_IDS or not THREAD_ID_RE.fullmatch(thread_id):
        raise ValueError("invalid or reserved thread_id; use 3-128 letters, digits, dots, underscores or hyphens, starting with a letter or digit")
    return thread_id


def get_thread_root(thread_id: str) -> Path:
    root = ROOT_BASE / validate_thread_id(thread_id)
    if root.is_symlink():
        raise ValueError("symlink thread roots are not allowed")
    return root


def require_existing_thread_root(thread_id: str) -> Path:
    root = get_thread_root(thread_id)
    if not root.is_dir():
        raise ValueError("thread directory does not exist; call create_thread first")
    return root


def safe_path(thread_id: str, path: str) -> Path:
    root = require_existing_thread_root(thread_id)
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("use a thread-relative path without '..'")
    p = root
    for part in relative.parts:
        p = p / part
        if p.is_symlink():
            raise ValueError("symlink paths are not supported")
    if p.resolve() != root and root not in p.resolve().parents:
        raise ValueError("path outside allowed thread root")
    return p


def limit(value: int, maximum: int = 10000) -> int:
    if not 1 <= value <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


def text_of(p: Path) -> str:
    if not p.is_file():
        raise ValueError("not a file")
    if p.stat().st_size > MAX_BYTES:
        raise ValueError("file exceeds 2 MiB text limit")
    data = p.read_bytes()
    if len(data) > MAX_BYTES or b"\0" in data:
        raise ValueError("file too large or binary")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("file is not UTF-8 text") from exc


def atomic_write(p: Path, content: str) -> None:
    data = content.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("content exceeds 2 MiB limit")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mcp-", dir=p.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.chmod(name, p.stat().st_mode & 0o777 if p.exists() else 0o640)
        os.replace(name, p)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def check_hash(p: Path, expected: str | None) -> None:
    if expected is not None:
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise ValueError("content hash mismatch; read the file again")


def walk_paths(root: Path, max_depth: int, exclude: list[str]):
    if not 0 <= max_depth <= 32:
        raise ValueError("max_depth must be between 0 and 32")
    for base, dirs, files in os.walk(root, followlinks=False):
        current = Path(base)
        depth = len(current.relative_to(root).parts)
        dirs[:] = sorted(d for d in dirs if d not in exclude and not (current / d).is_symlink())
        if depth >= max_depth:
            dirs[:] = []
        for name in sorted(dirs + files):
            p = current / name
            if name not in exclude and not p.is_symlink():
                yield p


@mcp.tool()
@log_tool_call("filesystem")
def list_threads() -> list[str]:
    ROOT_BASE.mkdir(parents=True, exist_ok=True)
    return sorted(p.name for p in ROOT_BASE.iterdir() if p.is_dir() and not p.is_symlink())


@mcp.tool()
@log_tool_call("filesystem")
def create_thread(thread_id: str) -> str:
    root = get_thread_root(thread_id)
    if root.exists():
        if root.is_dir():
            return f"thread directory already exists: {root}"
        raise ValueError("thread path exists but is not a directory")
    root.mkdir(parents=True)
    return f"created thread directory {root}"


@mcp.tool()
@log_tool_call("filesystem")
def delete_thread(thread_id: str, recursive: bool = False) -> str:
    root = require_existing_thread_root(thread_id)
    shutil.rmtree(root) if recursive else root.rmdir()
    return f"deleted thread directory {root}"


@mcp.tool()
@log_tool_call("filesystem")
def list_allowed_directories(thread_id: str) -> list[str]:
    return [str(require_existing_thread_root(thread_id))]


@mcp.tool()
@log_tool_call("filesystem")
def stat_path(thread_id: str, path: str) -> dict:
    """Return metadata for a thread-relative, non-symlink path."""
    p = safe_path(thread_id, path)
    st = p.stat()
    return {"path": path, "type": "directory" if p.is_dir() else "file", "size": st.st_size, "mtime": st.st_mtime, "permissions": oct(st.st_mode & 0o777)}


@mcp.tool()
@log_tool_call("filesystem")
def list_directory(thread_id: str, path: str = ".", details: bool = False, recursive: bool = False, max_depth: int = 8, max_entries: int = 1000):
    """List names by default; detailed or recursive mode returns bounded entries and truncation status."""
    p = safe_path(thread_id, path)
    if not p.is_dir():
        raise ValueError("not a directory")
    cap = limit(max_entries)
    if not details and not recursive:
        names = sorted(x.name for x in p.iterdir())
        if len(names) > cap:
            raise ValueError("too many entries; use details=True for bounded output")
        return names
    entries = []
    iterator = walk_paths(p, max_depth, [".git"]) if recursive else sorted(p.iterdir())
    for item in iterator:
        if len(entries) == cap:
            return {"entries": entries, "truncated": True}
        if item.is_symlink():
            entries.append({"path": str(item.relative_to(p)), "type": "symlink"})
        else:
            entries.append({"path": str(item.relative_to(p)), "type": "directory" if item.is_dir() else "file", "size": item.stat().st_size})
    return {"entries": entries, "truncated": False}


@mcp.tool()
@log_tool_call("filesystem")
def read_file(thread_id: str, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """Read UTF-8 text up to 2 MiB; optional inclusive, 1-based line range."""
    text = text_of(safe_path(thread_id, path))
    if start_line is None and end_line is None:
        return text
    start = 1 if start_line is None else start_line
    if start < 1 or (end_line is not None and end_line < start):
        raise ValueError("invalid line range")
    return "".join(text.splitlines(keepends=True)[start - 1:end_line])


@mcp.tool()
@log_tool_call("filesystem")
def write_file(thread_id: str, path: str, content: str, overwrite: bool = True, expected_sha256: str | None = None) -> str:
    """Atomically replace UTF-8 text; optionally require an existing content hash."""
    p = safe_path(thread_id, path)
    if p.exists() and not overwrite:
        raise ValueError("destination already exists")
    check_hash(p, expected_sha256)
    atomic_write(p, content)
    return f"wrote {p}"


@mcp.tool()
@log_tool_call("filesystem")
def create_directory(thread_id: str, path: str) -> str:
    p = safe_path(thread_id, path)
    p.mkdir(parents=True, exist_ok=True)
    return f"created {p}"


@mcp.tool()
@log_tool_call("filesystem")
def delete_path(thread_id: str, path: str, recursive: bool = False, missing_ok: bool = False) -> str:
    p = safe_path(thread_id, path)
    if p == require_existing_thread_root(thread_id):
        raise ValueError("use delete_thread to delete the thread root")
    if not p.exists():
        if missing_ok:
            return "path already absent"
        raise ValueError("path does not exist")
    if p.is_dir():
        shutil.rmtree(p) if recursive else p.rmdir()
    else:
        p.unlink()
    return f"deleted {p}"


def transfer_paths(thread_id: str, source: str, destination: str):
    src, dst = safe_path(thread_id, source), safe_path(thread_id, destination)
    root = require_existing_thread_root(thread_id)
    if src == root or dst == root or src == dst or src in dst.parents or dst in src.parents:
        raise ValueError("root, identical, or overlapping paths are not allowed")
    if not src.exists():
        raise ValueError("source does not exist")
    if src.is_dir():
        for base, dirs, files in os.walk(src, followlinks=False):
            if any((Path(base) / name).is_symlink() for name in dirs + files):
                raise ValueError("source tree contains a symlink")
    return src, dst


@mcp.tool()
@log_tool_call("filesystem")
def move_path(thread_id: str, source: str, destination: str, overwrite: bool = False) -> str:
    """Rename within a thread; replacement is allowed only for files."""
    src, dst = transfer_paths(thread_id, source, destination)
    if dst.exists() and (not overwrite or not src.is_file() or not dst.is_file()):
        raise ValueError("destination exists; overwrite supports files only")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    return f"moved {source} to {destination}"


@mcp.tool()
@log_tool_call("filesystem")
def copy_path(thread_id: str, source: str, destination: str, recursive: bool = False, overwrite: bool = False) -> str:
    """Copy files or explicitly recursive trees; existing directory destinations are rejected."""
    src, dst = transfer_paths(thread_id, source, destination)
    if dst.exists() and (not overwrite or not src.is_file() or not dst.is_file()):
        raise ValueError("destination exists; overwrite supports files only")
    if src.is_dir() and not recursive:
        raise ValueError("directory copy requires recursive=True")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
    return f"copied {source} to {destination}"


@mcp.tool()
@log_tool_call("filesystem")
def find_paths(thread_id: str, pattern: str, path: str = ".", max_results: int = 100, max_depth: int = 16) -> dict:
    """Match filenames or paths with glob syntax; skip .git and symlinks."""
    root = safe_path(thread_id, path)
    if not root.is_dir():
        raise ValueError("not a directory")
    cap, results = limit(max_results), []
    for p in walk_paths(root, max_depth, [".git"]):
        relative = str(p.relative_to(require_existing_thread_root(thread_id)))
        if fnmatch.fnmatchcase(p.name, pattern) or fnmatch.fnmatchcase(str(p.relative_to(root)), pattern):
            if len(results) == cap:
                return {"paths": results, "truncated": True}
            results.append(relative)
    return {"paths": results, "truncated": False}


@mcp.tool()
@log_tool_call("filesystem")
def search_text(thread_id: str, text: str, path: str = ".", max_results: int = 100, max_depth: int = 16) -> dict:
    """Literal line search; skip .git, symlinks, binary and oversized files. Scan at most 10000 paths."""
    if not text or "\n" in text:
        raise ValueError("provide nonempty single-line search text")
    root = safe_path(thread_id, path)
    iterator = [root] if root.is_file() else walk_paths(root, max_depth, [".git"])
    cap, results, skipped = limit(max_results), [], 0
    for index, p in enumerate(iterator):
        if index >= 10000:
            return {"matches": results, "skipped_files": skipped, "truncated": True}
        if not p.is_file():
            continue
        try:
            content = text_of(p)
        except (ValueError, OSError):
            skipped += 1
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if text in line:
                if len(results) == cap:
                    return {"matches": results, "skipped_files": skipped, "truncated": True}
                results.append({"path": str(p.relative_to(require_existing_thread_root(thread_id))), "line": number, "text": line[:1000]})
    return {"matches": results, "skipped_files": skipped, "truncated": False}


@mcp.tool()
@log_tool_call("filesystem")
def replace_text(thread_id: str, path: str, old_text: str, new_text: str, expected_matches: int = 1, expected_sha256: str | None = None) -> dict:
    """Replace exact text only when the nonoverlapping match count is as expected."""
    if not old_text or expected_matches < 1:
        raise ValueError("old_text must be nonempty and expected_matches positive")
    p = safe_path(thread_id, path)
    check_hash(p, expected_sha256)
    original = text_of(p)
    count = original.count(old_text)
    if count != expected_matches:
        raise ValueError(f"expected {expected_matches} matches, found {count}")
    atomic_write(p, original.replace(old_text, new_text))
    return {"path": path, "replacements": count}


def parse_patch(patch: str):
    if len(patch.encode("utf-8")) > MAX_BYTES:
        raise ValueError("patch exceeds 2 MiB limit")
    lines = patch.splitlines(keepends=True)
    i, changes = 0, []
    header = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)")
    def filename(line, prefix):
        name = line[4:].rstrip("\r\n")
        if not name or "\t" in name or name.startswith('"'):
            raise ValueError("use plain diff paths without timestamps or quoted names")
        if name.startswith(prefix):
            name = name[2:]
        return name
    while i < len(lines):
        if lines[i].startswith("diff --git ") or lines[i].startswith("index "):
            i += 1
            continue
        if not lines[i].startswith("--- ") or i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
            raise ValueError("expected unified diff file headers")
        old, new = filename(lines[i], "a/"), filename(lines[i + 1], "b/")
        if old == new == "/dev/null" or (old != "/dev/null" and new != "/dev/null" and old != new):
            raise ValueError("invalid headers or unsupported rename")
        i += 2
        hunks = []
        while i < len(lines) and lines[i].startswith("@@ "):
            match = header.fullmatch(lines[i].rstrip("\r\n"))
            if not match:
                raise ValueError("invalid hunk header")
            a, ac, b, bc = (int(match[1]), int(match[2] or 1), int(match[3]), int(match[4] or 1))
            i += 1
            body, removed, added = [], 0, 0
            while i < len(lines) and (removed < ac or added < bc):
                line = lines[i]
                if not line or line[0] not in " +-":
                    raise ValueError("invalid or incomplete hunk")
                kind, value = line[0], line[1:]
                removed += kind != "+"
                added += kind != "-"
                if removed > ac or added > bc:
                    raise ValueError("hunk line counts do not match")
                i += 1
                if i < len(lines) and lines[i].rstrip("\r\n") == "\\ No newline at end of file":
                    value = value.removesuffix("\n").removesuffix("\r")
                    i += 1
                body.append((kind, value))
            hunks.append((a, ac, b, bc, body))
        if not hunks:
            raise ValueError("each file needs at least one hunk")
        changes.append((old, new, hunks))
        if len(changes) > 100:
            raise ValueError("patch exceeds 100 files")
    if not changes:
        raise ValueError("empty patch")
    return changes


@mcp.tool()
@log_tool_call("filesystem")
def apply_patch(thread_id: str, patch: str, dry_run: bool = False) -> dict:
    """Apply strict unified text diffs with exact positions/context. Validate all files first. Per-file writes are atomic, but multi-file writes are not transactional."""
    planned, seen = [], set()
    for old, new, hunks in parse_patch(patch):
        name = new if new != "/dev/null" else old
        p = safe_path(thread_id, name)
        if p == require_existing_thread_root(thread_id) or p in seen:
            raise ValueError("root or duplicate patch target")
        seen.add(p)
        if old == "/dev/null":
            if p.exists():
                raise ValueError(f"creation target already exists: {name}")
            original = ""
        else:
            original = text_of(p)
        source, output, cursor = original.splitlines(keepends=True), [], 0
        for a, ac, b, bc, body in hunks:
            position = a if ac == 0 else a - 1
            if position < cursor or position > len(source):
                raise ValueError(f"invalid or overlapping hunk position: {name}")
            output.extend(source[cursor:position])
            cursor = position
            expected_new = b if bc == 0 else b - 1
            if expected_new != len(output):
                raise ValueError(f"new hunk position mismatch: {name}")
            for kind, value in body:
                if kind != "+":
                    if cursor >= len(source) or source[cursor] != value:
                        raise ValueError(f"patch context mismatch: {name}, line {cursor + 1}")
                    cursor += 1
                if kind != "-":
                    output.append(value)
        output.extend(source[cursor:])
        result = "".join(output)
        if new == "/dev/null" and result:
            raise ValueError("deletion patch must remove the complete file")
        if len(result.encode("utf-8")) > MAX_BYTES:
            raise ValueError("patched file exceeds 2 MiB limit")
        planned.append((p, original, result, old == "/dev/null", new == "/dev/null"))
    if not dry_run:
        for p, original, result, create, delete in planned:
            safe_path(thread_id, str(p.relative_to(require_existing_thread_root(thread_id))))
            if (create and p.exists()) or (not create and text_of(p) != original):
                raise ValueError("target changed during validation; no writes performed")
        applied = []
        try:
            for p, original, result, create, delete in planned:
                p.unlink() if delete else atomic_write(p, result)
                applied.append(str(p.relative_to(require_existing_thread_root(thread_id))))
        except OSError as exc:
            raise ValueError(f"write failed; files already applied: {applied}; {exc}") from exc
    return {"valid": True, "applied": not dry_run, "files": [str(p.relative_to(require_existing_thread_root(thread_id))) for p, *_ in planned], "transactional": False}


if __name__ == "__main__":
    ROOT_BASE.mkdir(parents=True, exist_ok=True)
    mcp.run(transport="streamable-http")
