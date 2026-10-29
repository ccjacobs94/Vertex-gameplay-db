#!/usr/bin/env bash
# Game Hub quality gate. Run from anywhere inside the repo:
#   scripts/check.sh           # full gate (required before commit / merge)
#   scripts/check.sh --quick   # skip the container test run (pre-commit hook)
#
# Exit code 0 = all gates passed. Any non-zero exit blocks the commit/merge.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)" || exit 2

QUICK=0
[[ "${1:-}" == "--quick" ]] && QUICK=1

IMAGE="${GAMEHUB_TEST_IMAGE:-game-hub-game-hub}"
BASE="${GAMEHUB_BASE_BRANCH:-develop}"
FAIL=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=1; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }

echo "== Game Hub quality gate =="

# 1. Branch policy (GitFlow) ---------------------------------------------------
branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" =~ ^(feature|bugfix|release|hotfix)/[a-z0-9._-]+$ ]] || [[ "$branch" =~ ^(main|develop)$ ]]; then
  pass "branch name '$branch'"
else
  fail "branch '$branch' does not follow GitFlow naming (feature/|bugfix/|release/|hotfix/<kebab-name>)"
fi

# 2. Secrets & live data must never be tracked ---------------------------------
leaked="$(git ls-files --cached -- '.env' '.env.*' 'data/*' '*.db' '*.db-wal' '*.db-shm' \
  '.agents/plugins/*/mcp_config.json' | grep -v '^\.env\.example$' || true)"
if [[ -z "$leaked" ]]; then
  pass "no secrets or database files tracked/staged"
else
  fail "secrets/data tracked or staged:"; echo "$leaked" | sed 's/^/         /'
fi

# 3. Python syntax / import-level compile -------------------------------------
if python3 - <<'PY'
import ast, pathlib, sys
bad = 0
for p in sorted(pathlib.Path(".").rglob("*.py")):
    if any(part in {".venv", "venv", "__pycache__", ".git"} for part in p.parts):
        continue
    try:
        ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
    except SyntaxError as e:
        print(f"         {p}:{e.lineno}: {e.msg}")
        bad = 1
sys.exit(bad)
PY
then pass "python syntax"; else fail "python syntax"; fi

# 4. JavaScript syntax ---------------------------------------------------------
if command -v node >/dev/null 2>&1; then
  js_ok=1
  for f in static/*.js; do
    if ! out="$(node --check "$f" 2>&1)"; then
      echo "$out" | sed 's/^/         /'; js_ok=0
    fi
  done
  ((js_ok)) && pass "javascript syntax" || fail "javascript syntax"
else
  warn "node not installed; skipped JS syntax check"
fi

# 5. PWA cache bust: static asset changes require a CACHE_NAME bump ------------
if git rev-parse --verify --quiet "$BASE" >/dev/null && [[ "$branch" != "$BASE" ]]; then
  merge_base="$(git merge-base HEAD "$BASE")"
  static_changed="$( { git diff --name-only "$merge_base" -- static/; git diff --name-only HEAD -- static/; } \
    | grep -v '^static/sw\.js$' | sort -u)"
  if [[ -n "$static_changed" ]]; then
    old_cache="$(git show "$merge_base:static/sw.js" 2>/dev/null | grep -o 'vertex-pwa-v[0-9]*' | head -1)"
    new_cache="$(grep -o 'vertex-pwa-v[0-9]*' static/sw.js | head -1)"
    if [[ "$old_cache" != "$new_cache" ]]; then
      pass "service worker cache bumped ($old_cache -> $new_cache)"
    else
      fail "static/ changed but static/sw.js CACHE_NAME is still '$new_cache' (bump it)"
    fi
  else
    pass "no static asset changes (cache bump not needed)"
  fi
fi

# 6. Unit + invariant tests ----------------------------------------------------
# Runs inside the app image (all deps present) with the working tree mounted
# read-only and the DB pointed at a throwaway path, so the live
# data/games.db can never be touched by a test.
if ((QUICK)); then
  warn "--quick: container test run skipped (run full gate before merge)"
elif docker image inspect "$IMAGE" >/dev/null 2>&1; then
  if docker run --rm --network none \
      -v "$PWD":/src:ro -w /src \
      -e PYTHONPATH=/src -e PYTHONDONTWRITEBYTECODE=1 \
      -e GAMEHUB_DB_PATH=/tmp/gate-games.db \
      --entrypoint python "$IMAGE" -m unittest discover -s tests 2>&1 | sed 's/^/         /'; then
    pass "unit + invariant tests (container)"
  else
    fail "unit + invariant tests (container)"
  fi
else
  warn "image '$IMAGE' missing; falling back to host python (some tests may skip)"
  if PYTHONPATH=. GAMEHUB_DB_PATH=/tmp/gate-games.db PYTHONDONTWRITEBYTECODE=1 \
      python3 -m unittest discover -s tests 2>&1 | sed 's/^/         /'; then
    pass "unit tests (host)"
  else
    fail "unit tests (host)"
  fi
fi

echo
if ((FAIL)); then echo "Quality gate FAILED"; exit 1; fi
echo "Quality gate PASSED"
