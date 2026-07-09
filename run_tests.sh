#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/http"
python3 -m py_compile fs_server.py git_server.py lib/logging_utils.py tests/test_logging_utils.py
python3 tests/test_logging_utils.py
