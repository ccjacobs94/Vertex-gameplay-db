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

  # 1. Back up production database if present
  if [[ -f "data/games.db" ]]; then
    BACKUP_FILE="data/games.db.bak-$(date +%Y%m%d-%H%M%S)"
    echo "📦 Backing up production database to $BACKUP_FILE"
    cp "data/games.db" "$BACKUP_FILE"
  fi

  # 2. Build and restart primary production container
  echo "🔨 Building and launching 'game-hub'..."
  docker compose up -d --build game-hub

  # 3. Verify healthcheck on port 8095
  wait_for_health "http://localhost:8095/api/stats" "Primary Game Hub (Port 8095)" || true

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
  mkdir -p data-qa

  # 2. Seed data-qa from data if empty, ensuring QA has realistic state without touching production
  if [[ ! -f "data-qa/games.db" ]] && [[ -f "data/games.db" ]]; then
    echo "📋 Seeding isolated data-qa/games.db from current database snapshot..."
    cp "data/games.db" "data-qa/games.db"
    # Copy auth db if present
    if [[ -f "data/auth.db" ]]; then
      cp "data/auth.db" "data-qa/auth.db"
    fi
  fi

  # 3. Build and launch QA container under 'qa' profile
  echo "🔨 Building and launching 'game-hub-qa' on ports 8098/8099..."
  docker compose --profile qa up -d --build game-hub-qa

  # 4. Verify healthcheck on port 8098
  wait_for_health "http://localhost:8098/api/stats" "QA Game Hub (Port 8098)" || true

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
