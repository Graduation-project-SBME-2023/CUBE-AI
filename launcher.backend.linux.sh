#!/usr/bin/env bash
# ─────────────────────────────────────────────
#  OHIF-Standalone launcher  ·  Backend only
#  Double-click this file (or: bash launcher.backend.linux.sh)
#
#  - No frontend: use launcher.linux.sh for both.
#  - No image updates: runs whatever images are already on this
#    machine (--pull never). Use launcher.linux.sh when you
#    actually want to fetch newer images.
#  - Applies the storage changes: compose recreates only the
#    containers whose spec changed, which is what makes the new
#    Backend/storage mounts and BACKEND_STORAGE_ROOT / *_DIR env
#    vars take effect.
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
echo "  OHIF-Standalone Backend-only launcher"
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
echo
info "Selected backend: $BACKEND_MODE"
warn "Frontend will NOT be started. Run launcher.linux.sh if you need the viewer."
echo

# ── 5. Storage migration ─────────────────────
# Relocates pre-restructure runtime data into Backend/storage. Silent no-op
# once migrated, and on a fresh clone with nothing to move.
if command -v python3 &>/dev/null; then
  PYTHON_BIN="python3"
elif command -v python &>/dev/null; then
  PYTHON_BIN="python"
else
  PYTHON_BIN=""
fi

if [[ -n "$PYTHON_BIN" ]]; then
  if ! "$PYTHON_BIN" "$COMPOSE_PROJECT_DIR/Backend/scripts/migrate_storage.py" --auto; then
    abort "Storage migration failed. Your data has not been lost — see the error above.
       A rollback script was written to Backend/storage/_migrated-legacy/."
  fi
else
  warn "Python not found; skipping the storage migration check."
  warn "If this is an upgrade from an older checkout, run Backend/scripts/migrate_storage.py --execute manually."
fi

# ── 6. Launch ────────────────────────────────
warn "Starting backend Docker containers..."
warn "Using the images already on this machine - nothing will be pulled or updated."
warn "Only containers whose config changed (storage mounts, storage env vars)"
warn "are recreated; everything else is left running as-is."
echo

# --pull never          : never contact the registry, use the local image only.
# (no --force-recreate) : compose recreates a container only when its spec
#                         changed, so the storage volume/env edits land while
#                         unrelated services keep their current containers.
if ! docker compose \
  -f "$BACKEND_COMPOSE" \
  up \
  --pull never; then
  abort "docker compose failed. Check the output above for errors.
       If it complains that an image is missing, that image was never pulled on
       this machine - run launcher.linux.sh (or 'docker compose -f \"$BACKEND_COMPOSE\" pull')
       once to fetch it."
fi

# ── 7. Final message ─────────────────────────
echo
info "Backend available at:"
info "  Agents API:  http://localhost:8150"
info "  Control API: http://localhost:8001"
info "  MONAI Label: http://localhost:8000"
info "  Orthanc:     http://localhost:8043"
info "  Postgres:    localhost:5433"
