#!/usr/bin/env bash
# ─────────────────────────────────────────────
#  OHIF-Standalone launcher  ·  Production / GPU
#  Double-click this file (or: bash launch.sh)
# ─────────────────────────────────────────────
set -euo pipefail

# ── helpers ──────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✔]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
abort() { echo -e "${RED}[✘]${NC} $*"; echo; exit 1; }

# Keep the terminal window open on macOS if double-clicked from Finder
if [[ "$(uname)" == "Darwin" ]] && [[ "$TERM_PROGRAM" == "" ]]; then
  osascript -e 'tell application "Terminal" to activate' 2>/dev/null || true
fi

echo
echo "  OHIF-Standalone  ·  Production launcher"
echo "  ─────────────────────────────────────────"
echo

# ── 1. Docker ────────────────────────────────
if ! command -v docker &>/dev/null; then
  abort "Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ then re-run this script."
fi

if ! docker info &>/dev/null; then
  abort "Docker Desktop is not running.
       Please open Docker Desktop, wait until it shows 'Docker is running', then double-click this script again."
fi
info "Docker is running."

# ── 2. Ollama ────────────────────────────────
if ! command -v ollama &>/dev/null; then
  abort "Ollama is not installed. Download it from https://ollama.com/download then re-run this script."
fi

# Try the HTTP health endpoint first; fall back to 'ollama list'
if curl -sf http://localhost:11434 &>/dev/null || ollama list &>/dev/null 2>&1; then
  info "Ollama is running."
else
  abort "Ollama does not appear to be running.
       On macOS:  open the Ollama app from your Applications folder.
       On Linux:  run 'ollama serve' in a separate terminal.
       Then double-click this script again."
fi

# ── 3. Environment variable ───────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export COMPOSE_PROJECT_DIR="$SCRIPT_DIR"
info "COMPOSE_PROJECT_DIR set to: $COMPOSE_PROJECT_DIR"

# ── 4. Launch ─────────────────────────────────
echo
warn "Starting containers (GPU · production mode)…"
warn "First run will download images — this may take several minutes."
echo

docker compose \
  -f "$COMPOSE_PROJECT_DIR/Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml" \
  -f "$COMPOSE_PROJECT_DIR/Backend/docker-compose.backend.gpu.yml" \
  up

echo
info "Application is available at http://localhost"