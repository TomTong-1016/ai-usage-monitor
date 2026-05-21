#!/usr/bin/env bash
# import-credentials.sh — Import credentials.json exported by the Chrome extension
#
# Usage:
#   bash import-credentials.sh ~/Downloads/credentials.json
#
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET}  $*"; }
error()   { echo -e "${RED}✗${RESET}  $*"; exit 1; }

CREDS="${1:-}"

if [ -z "$CREDS" ]; then
  error "Usage: bash import-credentials.sh <path-to-credentials.json>"
fi

if [ ! -f "$CREDS" ]; then
  error "File not found: $CREDS"
fi

if ! command -v python3 &>/dev/null; then
  error "Python 3 is required to parse JSON"
fi

echo ""
echo -e "${BOLD}Importing credentials from:${RESET} $CREDS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

python3 - "$CREDS" <<'PYEOF'
import json, sys, os
from pathlib import Path

creds_file = sys.argv[1]
root = Path(__file__).parent if "__file__" in dir() else Path(".")

# Locate project root (where server.py lives)
# When run as "bash import-credentials.sh ...", __file__ is the temp script, so use cwd
root = Path(os.getcwd())

data = json.loads(Path(creds_file).read_text())

written = []
skipped = []

# ── Cookies ──────────────────────────────────────────────────────────────────
cookie_dir = root / "cookie"
cookie_dir.mkdir(exist_ok=True)
for filename, content in data.get("cookies", {}).items():
    if not content.strip():
        skipped.append(f"cookie/{filename} (empty)")
        continue
    (cookie_dir / filename).write_text(content)
    written.append(f"cookie/{filename}")

# ── Headers ──────────────────────────────────────────────────────────────────
header_dir = root / "header-txt"
header_dir.mkdir(exist_ok=True)
for filename, content in data.get("headers", {}).items():
    if not content.strip():
        skipped.append(f"header-txt/{filename} (empty)")
        continue
    (header_dir / filename).write_text(content)
    written.append(f"header-txt/{filename}")

# ── config.json (org IDs) ─────────────────────────────────────────────────────
config_path = root / "config.json"
existing_config = {}
if config_path.exists():
    try:
        existing_config = json.loads(config_path.read_text())
    except json.JSONDecodeError:
        pass

updated = False
for key in ("claude_org_id",):
    val = data.get(key, "")
    if val and val != existing_config.get(key):
        existing_config[key] = val
        updated = True

if updated:
    config_path.write_text(json.dumps(existing_config, indent=2, ensure_ascii=False) + "\n")
    written.append("config.json")

# ── Summary ───────────────────────────────────────────────────────────────────
for f in written:
    print(f"  \033[32m✓\033[0m  {f}")
for f in skipped:
    print(f"  \033[33m⚠\033[0m  skipped: {f}")

print()
if written:
    print(f"\033[32m✓\033[0m  {len(written)} file(s) imported successfully")
else:
    print("\033[33m⚠\033[0m  No files were written — credentials.json may be empty")

PYEOF

echo ""
echo "Start the dashboard:"
echo "  venv/bin/uvicorn server:app --host 127.0.0.1 --port 8765"
echo ""
