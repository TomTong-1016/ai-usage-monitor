#!/usr/bin/env bash
# install.sh — One-command setup for AI Usage Monitor (macOS / Linux)
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

info()    { echo -e "${BOLD}$*${RESET}"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET}  $*"; }
error()   { echo -e "${RED}✗${RESET}  $*"; exit 1; }

echo ""
info "AI Usage Monitor — setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Python check ──────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  error "Python 3 not found.\n  Install from https://python.org or via Homebrew: brew install python3"
fi
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PY_VERSION found"

# ── 2. Create virtualenv ─────────────────────────────────────────────────────
if [ ! -d venv ]; then
  echo "Creating virtual environment…"
  python3 -m venv venv
  success "Virtual environment created"
else
  success "Virtual environment already exists"
fi

# ── 3. Install dependencies ──────────────────────────────────────────────────
echo "Installing Python dependencies…"
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt
success "Dependencies installed"

# ── 4. Create credential directories ────────────────────────────────────────
mkdir -p cookie header-txt request_overrides
# Ensure .gitkeep exists so directories are tracked by git
touch cookie/.gitkeep header-txt/.gitkeep request_overrides/.gitkeep
success "Credential directories ready"

# ── 5. Config file ───────────────────────────────────────────────────────────
if [ ! -f config.json ]; then
  cp config.json.example config.json
  warn "config.json created from template — org IDs will be filled automatically when you export credentials"
else
  success "config.json already exists"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success "Installation complete!"
echo ""
echo "Next steps:"
echo ""
echo "  1. Install the Chrome extension:"
echo "     Chrome → Settings → Extensions → Developer mode → Load unpacked"
echo "     Select: $(pwd)/chrome-extension"
echo ""
echo "  2. Visit each platform in Chrome while logged in."
echo "     Then click the extension icon and press 「导出 credentials.json」"
echo ""
echo "  3. Import the exported credentials:"
echo "     bash import-credentials.sh ~/Downloads/credentials.json"
echo ""
echo "  4. Start the dashboard:"
echo "     venv/bin/uvicorn server:app --host 127.0.0.1 --port 8765"
echo "     Then open http://localhost:8765"
echo ""
