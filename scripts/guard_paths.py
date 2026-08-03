#!/usr/bin/env python3
"""Hook Claude Code: bloqueia escrita direta em artefatos compilados/governados."""
from __future__ import annotations
import json
import sys
from pathlib import Path

payload = json.load(sys.stdin)
tool_input = payload.get("tool_input") or {}
raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
path = Path(str(raw_path)).as_posix().lstrip("./")
blocked = ("dist/graph/", "catalog/factsheets.json", "workspace/harness/")
if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in blocked):
    print(f"Escrita bloqueada em {path}. Use o harness ou o processo governado correspondente.", file=sys.stderr)
    raise SystemExit(2)
raise SystemExit(0)
