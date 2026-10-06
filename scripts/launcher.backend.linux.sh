#!/usr/bin/env bash
# ─────────────────────────────────────────────
#  OHIF-Standalone launcher  ·  Backend only
#  Double-click this file (or: bash launcher.backend.linux.sh)
#
#  - No frontend: use launcher.linux.sh for both.
#  - No image updates: reuses whatever images are already on this
#    machine (--pull missing) and only pulls one that's missing
#    entirely. Use launcher.linux.sh when you actually want to
#    fetch newer images.
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

# Bounds a command with a hard timeout so a stuck call (e.g. 'ollama list' or
# 'docker info' while their backend is still starting up) can never freeze
# this whole launcher. Falls back to running unbounded if no timeout tool is
# available (plain macOS without coreutils).
with_timeout() {
  local secs="$1"; shift
  if command -v timeout &>/dev/null; then
    timeout "$secs" "$@"
  elif command -v gtimeout &>/dev/null; then
    gtimeout "$secs" "$@"
  else
    "$@"
  fi
}

echo
echo "  OHIF-Standalone Backend-only launcher"
echo "  ─────────────────────────────────────────"
echo -e "${NC}"

# Check every required application before trying to start either one so a new
# user gets the complete installation list in a single run.
missing_prerequisites=()
if ! command -v docker &>/dev/null; then
  missing_prerequisites+=("Docker: https://docs.docker.com/engine/install/")
fi
if ! command -v ollama &>/dev/null; then
  missing_prerequisites+=("Ollama: https://ollama.com/download")
fi

if (( ${#missing_prerequisites[@]} > 0 )); then
  warn "The following required software is not installed or is not available in PATH:"
  for prerequisite in "${missing_prerequisites[@]}"; do
    echo "  - $prerequisite"
  done
  abort "Install the missing software, start it once to finish setup, then re-run this script. If it is already installed, open a new terminal so PATH is refreshed."
fi

info "Docker is installed."
info "Ollama is installed."

# ── 1. Docker ────────────────────────────────
# Don't make the user open Docker by hand - start it here and wait.
start_docker() {
  case "$(uname)" in
    Darwin)
      open -a Docker 2>/dev/null || open -a "Docker Desktop" 2>/dev/null || true
      ;;
    *)
      # Docker Desktop for Linux first, then the system daemon.
      if command -v systemctl &>/dev/null; then
        systemctl --user start docker-desktop 2>/dev/null \
          || sudo systemctl start docker 2>/dev/null || true
      else
        sudo service docker start 2>/dev/null || true
      fi
      ;;
  esac
}

# Launch the Docker app whenever its process isn't already running - even if
# the engine already answers (e.g. a lingering daemon) - so the app actually
# opens instead of silently relying on a background engine.
docker_app_running() {
  case "$(uname)" in
    Darwin) pgrep -x Docker &>/dev/null || pgrep -f "Docker Desktop" &>/dev/null ;;
    *)      pgrep -f "Docker Desktop" &>/dev/null || pgrep -x dockerd &>/dev/null ;;
  esac
}

if ! docker_app_running; then
  warn "Docker app is not running - launching it..."
  start_docker
fi

if ! with_timeout 10 docker info &>/dev/null; then
  warn "Waiting for the Docker engine to come up (up to 180s)..."
  waited=0
  while ! with_timeout 10 docker info &>/dev/null; do
    if (( waited >= 180 )); then
      abort "Docker did not become ready within 180 seconds.
       Start Docker manually, wait until it reports 'Docker is running', then re-run this script."
    fi
    if (( waited % 15 == 0 )); then
      warn "  ...still waiting on Docker (${waited}s elapsed)"
    fi
    sleep 3
    waited=$((waited + 3))
  done
fi
info "Docker is running."

# ── 2. Ollama ────────────────────────────────
# Same idea: if Ollama isn't answering, start it ourselves - the macOS app when
# present, otherwise a headless 'ollama serve' in the background.
# The HTTP port is the only signal that actually matters: it's exactly what
# the agents container talks to (OLLAMA_BASE_URL=http://host.docker.internal:11434).
# 'ollama list' is deliberately NOT used here - on some installs it silently
# launches the app/server as a side effect just by being invoked, and on
# others it can exit 0 even when nothing is actually listening, so its exit
# code is not a trustworthy readiness signal either way.
ollama_ready() {
  curl -sf --max-time 3 http://localhost:11434 &>/dev/null
}

if ! ollama_ready; then
  warn "Ollama is not responding - starting it now..."
  if [[ "$(uname)" == "Darwin" ]] && open -a Ollama 2>/dev/null; then
    :
  else
    nohup ollama serve >/dev/null 2>&1 &
    disown 2>/dev/null || true
  fi
  warn "Waiting for Ollama to accept connections (up to 60s)..."
  waited=0
  while ! ollama_ready; do
    if (( waited >= 60 )); then
      abort "Ollama did not become ready within 60 seconds.
       Start it manually (run 'ollama serve' or open the Ollama app), then re-run this script."
    fi
    if (( waited % 10 == 0 )); then
      warn "  ...still waiting on Ollama (${waited}s elapsed)"
    fi
    sleep 3
    waited=$((waited + 3))
  done
fi
info "Ollama is running."

# ── 3. Environment variable ───────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_ENV="$PROJECT_ROOT/deploy/.env"
export COMPOSE_PROJECT_DIR="$PROJECT_ROOT"

if [[ ! -f "$BACKEND_ENV" ]]; then
  abort "Backend environment file not found: $BACKEND_ENV
       Copy deploy/.env.example to deploy/.env and try again."
fi

info "COMPOSE_PROJECT_DIR set to: $COMPOSE_PROJECT_DIR"

# ── 4. Backend compose (GPU only) ────────────
# The CPU backend variant has been removed - this stack is GPU-only.
BACKEND_COMPOSE="$COMPOSE_PROJECT_DIR/deploy/docker-compose.backend.gpu.yml"
BACKEND_MODE="GPU"
echo
info "Backend mode: $BACKEND_MODE"
warn "Frontend will NOT be started. Run scripts/launcher.linux.sh if you need the viewer."
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
warn "Images already on this machine are reused as-is; only ones missing entirely are pulled."
warn "Only containers whose config changed (storage mounts, storage env vars)"
warn "are recreated; everything else is left running as-is."
echo

# --pull missing        : reuse the pre-built images (agents, monailabel,
#                         orthanc, postgres) already on this machine as-is;
#                         only pull one that isn't present at all. Never
#                         re-pulls/updates an image that's already here -
#                         `--pull never` used to be here, but that hard-fails
#                         with "No such image" the moment any required image
#                         hasn't been pulled yet.
# --build               : application-api has a `build:`
#                         section and is meant to always run the current
#                         checkout's code; without it, `up` can reuse an
#                         already-built local image tag and silently run
#                         stale migrations/code (database_audit.md D-33).
#                         --build only affects services that declare
#                         `build:`, so the "nothing re-pulled" behavior for
#                         the rest is unaffected.
# (no --force-recreate) : compose recreates a container only when its spec
#                         changed, so the storage volume/env edits land while
#                         unrelated services keep their current containers.
if ! docker compose \
  --env-file "$BACKEND_ENV" \
  -f "$BACKEND_COMPOSE" \
  up \
  --pull missing \
  --build \
  --detach \
  --wait \
  --wait-timeout 3600; then
  abort "docker compose failed. Check the output above for errors.
       If it complains that an image is missing, that image was never pulled on
       this machine - run scripts/launcher.linux.sh (or 'docker compose --env-file \"$BACKEND_ENV\" -f \"$BACKEND_COMPOSE\" pull')
       once to fetch it."
fi

# ── 7. Final message ─────────────────────────
echo
# Print recent startup output once, then reserve the project-level message
# for the point at which Compose has confirmed every service is ready.
docker compose \
  --env-file "$BACKEND_ENV" \
  -f "$BACKEND_COMPOSE" \
  logs --tail 20
echo
info "Backend available at:"
info "  Agents API:  http://localhost:8150"
info "  Application API: http://localhost:8001"
info "  MONAI Label: http://localhost:8000"
info "  Orthanc:     http://localhost:8043"
info "  Postgres:    localhost:5433"
warn "Following container logs. Press Ctrl+C to stop viewing logs; containers will keep running."
echo
docker compose \
  --env-file "$BACKEND_ENV" \
  -f "$BACKEND_COMPOSE" \
  logs --follow --tail 0
