"""Test path bootstrap so `vertex` imports cleanly under both host python and PYTHONPATH=/src."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
MIGRATIONS_DIR = REPO_ROOT / "migrations"
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
