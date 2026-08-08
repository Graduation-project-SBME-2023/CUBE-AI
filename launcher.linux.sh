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
echo "  OHIF-Standalone Interactive launcher"
echo "  ─────────────────────────────────────────"
echo -e "${NC}"

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

# ── 4. Backend Selection ─────────────────────
echo "Select backend mode:"
echo "  1) GPU"
echo "  2) CPU"
echo
while true; do
  read -rp "Choice [1-2]: " BACKEND_CHOICE
  case "$BACKEND_CHOICE" in
    1)
      BACKEND_COMPOSE="$COMPOSE_PROJECT_DIR/Backend/docker-compose.backend.gpu.yml"
      BACKEND_MODE="GPU"
      break
      ;;
    2)
      BACKEND_COMPOSE="$COMPOSE_PROJECT_DIR/Backend/docker-compose.backend.cpu.yml"
      BACKEND_MODE="CPU"
      break
      ;;
    *)
      warn "Invalid selection."
      ;;
  esac
done

# ── 5. Frontend Selection ────────────────────
echo "Select frontend mode:"
echo "  1) Docker (production)"
echo "  2) Yarn (development)"
echo
while true; do
  read -rp "Choice [1-2]: " FRONTEND_CHOICE
  case "$FRONTEND_CHOICE" in
    1)
      FRONTEND_MODE="Docker"
      FRONTEND_COMPOSE="$COMPOSE_PROJECT_DIR/Frontend/OHIF-Viewer/docker-compose.frontend.prod.yml"
      break
      ;;
    2)
      FRONTEND_MODE="Yarn"
      break
      ;;
    *)
      warn "Invalid selection."
      ;;
  esac
done
echo
info "Selected backend: $BACKEND_MODE"
info "Selected frontend: $FRONTEND_MODE"
echo

# ── 6. Launch ────────────────────────────────
if [[ "$FRONTEND_MODE" == "Docker" ]]; then
  warn "Starting Docker containers..."
  warn "First run may take several minutes."
  echo
  docker compose \
    -f "$BACKEND_COMPOSE" \
    -f "$FRONTEND_COMPOSE" \
    up
else
  # ── Yarn checks ───────────────────────────
  if ! command -v yarn &>/dev/null; then
    abort "Yarn is not installed.
Install it with:
npm install -g yarn"
  fi
  FRONTEND_DIR="$COMPOSE_PROJECT_DIR/Frontend/OHIF-Viewer"
  if [[ ! -d "$FRONTEND_DIR" ]]; then
    abort "Frontend directory not found:
$FRONTEND_DIR"
  fi
  warn "Starting backend containers..."
  docker compose -f "$BACKEND_COMPOSE" up -d
  echo
  warn "Starting OHIF frontend with Yarn..."
  echo
  cd "$FRONTEND_DIR"
  yarn run dev
fi
echo
info "Application available at:"
if [[ "$FRONTEND_MODE" == "Docker" ]]; then
  info "  Frontend: http://localhost"
else
  info "  Frontend: http://localhost:3000"
fi