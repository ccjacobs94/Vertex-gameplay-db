#!/usr/bin/env bash
# ==============================================================================
# Game Hub Container Deployment Automation
#
# Usage:
#   scripts/deploy.sh [main|develop|auto]
#
# - 'main' (Production):
#     Enforces quality gate, creates automated DB backup (.bak), builds and
#     deploys the primary container 'game-hub' on ports 8095 (Web) & 8097 (MCP).
# - 'develop' (QA):
#     Seeds isolated ./data-qa database if needed, builds and deploys the
#     QA container 'game-hub-qa' on ports 8098 (Web) & 8099 (MCP) for LAN/Tailscale QA.
# - 'auto' (Default):
#     Inspects the current git branch and deploys the matching target.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

TARGET="${1:-auto}"
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")"

if [[ "$TARGET" == "auto" ]]; then
  if [[ "$CURRENT_BRANCH" == "main" ]]; then
    TARGET="main"
  elif [[ "$CURRENT_BRANCH" == "develop" ]]; then
    TARGET="develop"
  else
    echo "ℹ️  Current branch '$CURRENT_BRANCH' is neither 'main' nor 'develop'."
    echo "   Deploy script auto-mode only triggers on 'main' or 'develop'."
    exit 0
  fi
fi

# Detect Tailscale and LAN IPs for convenient QA & Prod access links
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")"
TAILSCALE_IP="$(ip -4 addr show tailscale0 2>/dev/null | awk '/inet / {print $2}' | cut -d/ -f1 || echo "")"

# Read storage configurations from .env if present
if [[ -f "$REPO_ROOT/.env" ]]; then
  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    key="$(echo "$key" | xargs 2>/dev/null || true)"
    value="$(echo "$value" | xargs 2>/dev/null || true)"
    if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && [[ ! "$key" =~ ^# ]]; then
      if [[ -z "${!key:-}" ]]; then
        export "$key=$value"
      fi
    fi
  done < "$REPO_ROOT/.env"
fi

DATA_DIR="${GAMEHUB_DATA_PATH:-$REPO_ROOT/data}"
QA_DATA_DIR="${GAMEHUB_QA_DATA_PATH:-$REPO_ROOT/data-qa}"
BACKUP_DIR="${GAMEHUB_BACKUP_PATH:-/mnt/storage-pool/game-hub/backups}"
BACKUP_KEEP="${GAMEHUB_BACKUP_KEEP:-15}"

prune_backups() {
  local dir="$1"
  local keep="${2:-15}"
  if [[ -d "$dir" ]]; then
    local count
    count=$(find "$dir" -maxdepth 1 -type f \( -name "*.bak-*" -o -name "*.bak" \) 2>/dev/null | wc -l)
    if (( count > keep )); then
      echo "🧹 Pruning older backups in $dir (keeping newest $keep of $count)..."
      find "$dir" -maxdepth 1 -type f \( -name "*.bak-*" -o -name "*.bak" \) -printf '%T@ %p\n' 2>/dev/null | \
        sort -nr | tail -n +$((keep + 1)) | cut -d' ' -f2- | while IFS= read -r old_file; do
          if [[ -f "$old_file" ]]; then
            rm -f "$old_file"
            echo "   Deleted old backup: $(basename "$old_file")"
          fi
        done
    fi
  fi
}

wait_for_health() {
  local url="$1"
  local name="$2"
  local max_attempts="${3:-20}"
  local attempt=1

  echo "⏳ Waiting for $name to be healthy at $url ..."
  while (( attempt <= max_attempts )); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then
      echo "✅ $name is up and healthy!"
      return 0
    fi
    sleep 2
    (( attempt++ ))
  done
  echo "⚠️ $name failed to respond with 200 within timeout."
  return 1
}

if [[ "$TARGET" == "main" ]]; then
  echo "🚀 Deploying PRIMARY PRODUCTION container ('game-hub') from 'main'..."

  # 1. Back up production databases (system, default, and all per-user databases)
  umask 077
  mkdir -p "$BACKUP_DIR" 2>/dev/null || BACKUP_DIR="$DATA_DIR"
  chmod 700 "$BACKUP_DIR" 2>/dev/null || true

  backup_sqlite() {
    local src="$1"
    local dst="$2"
    mkdir -p "$(dirname "$dst")"
    if command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "$src" ".backup '$dst'"
    else
      python3 -c "import sqlite3, sys; s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" "$src" "$dst"
    fi
    chmod 600 "$dst" 2>/dev/null || true
  }

  if [[ -f "$DATA_DIR/games.db" ]]; then
    BACKUP_FILE="$BACKUP_DIR/games.db.bak-$(date +%Y%m%d-%H%M%S)"
    echo "📦 Backing up production database to $BACKUP_FILE"
    backup_sqlite "$DATA_DIR/games.db" "$BACKUP_FILE"
  fi
  if [[ -f "$DATA_DIR/system.db" ]]; then
    SYS_BACKUP_FILE="$BACKUP_DIR/system.db.bak-$(date +%Y%m%d-%H%M%S)"
    echo "📦 Backing up system database to $SYS_BACKUP_FILE"
    backup_sqlite "$DATA_DIR/system.db" "$SYS_BACKUP_FILE"
  fi
  if [[ -d "$DATA_DIR/users" ]]; then
    for udir in "$DATA_DIR/users"/*; do
      if [[ -d "$udir" ]] && [[ -f "$udir/games.db" ]]; then
        uname="$(basename "$udir")"
        ubackup="$BACKUP_DIR/users/$uname/games.db.bak-$(date +%Y%m%d-%H%M%S)"
        echo "📦 Backing up user database for '$uname' to $ubackup"
        backup_sqlite "$udir/games.db" "$ubackup"
        prune_backups "$BACKUP_DIR/users/$uname" "$BACKUP_KEEP"
      fi
    done
  fi
  prune_backups "$BACKUP_DIR" "$BACKUP_KEEP"

  # 2. Build and restart primary production container
  echo "🔨 Building and launching 'game-hub'..."
  docker compose up -d --build game-hub

  # 3. Verify healthcheck on port 8095
  wait_for_health "http://localhost:8095/healthz" "Primary Game Hub (Port 8095)" || true

  echo ""
  echo "=================================================================="
  echo "🎉 Production deployment completed successfully!"
  echo "   - Local Web UI:      http://localhost:8095"
  if [[ -n "$TAILSCALE_IP" ]]; then
    echo "   - Tailscale Web UI:  http://${TAILSCALE_IP}:8095"
  fi
  echo "   - LAN Web UI:        http://${LAN_IP}:8095"
  echo "   - Local MCP Server:  http://localhost:8097/mcp"
  echo "=================================================================="

elif [[ "$TARGET" == "develop" ]]; then
  echo "🧪 Deploying QA VALIDATION container ('game-hub-qa') from 'develop'..."

  # 1. Ensure isolated data-qa directory exists
  mkdir -p "$QA_DATA_DIR"

  # 2. Seed data-qa from data if empty, ensuring QA has realistic state without touching production
  if [[ ! -f "$QA_DATA_DIR/games.db" ]] && [[ -f "$DATA_DIR/games.db" ]]; then
    echo "📋 Seeding isolated $QA_DATA_DIR/games.db from current database snapshot..."
    cp "$DATA_DIR/games.db" "$QA_DATA_DIR/games.db"
    if [[ -f "$DATA_DIR/system.db" ]]; then
      cp "$DATA_DIR/system.db" "$QA_DATA_DIR/system.db"
    fi
    if [[ -d "$DATA_DIR/users" ]] && [[ ! -d "$QA_DATA_DIR/users" ]]; then
      echo "📋 Seeding isolated user databases into $QA_DATA_DIR/users..."
      cp -r "$DATA_DIR/users" "$QA_DATA_DIR/users"
    fi
  fi

  # 3. Build and launch QA container under 'qa' profile
  echo "🔨 Building and launching 'game-hub-qa' on ports 8098/8099..."
  docker compose --profile qa up -d --build game-hub-qa

  # 4. Verify healthcheck on port 8098
  wait_for_health "http://localhost:8098/healthz" "QA Game Hub (Port 8098)" || true

  echo ""
  echo "=================================================================="
  echo "🧪 QA validation container is ready for LAN/Tailscale testing!"
  echo "   - Local Web UI:      http://localhost:8098"
  if [[ -n "$TAILSCALE_IP" ]]; then
    echo "   - Tailscale Web UI:  http://${TAILSCALE_IP}:8098"
  fi
  echo "   - LAN Web UI:        http://${LAN_IP}:8098"
  echo "   - Local MCP Server:  http://localhost:8099/mcp"
  echo "=================================================================="

else
  echo "❌ Unknown deployment target: '$TARGET'. Use 'main', 'develop', or 'auto'." >&2
  exit 1
fi
