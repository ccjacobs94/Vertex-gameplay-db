#!/usr/bin/env python3
"""Offline frontend build pipeline wrapper delegating to vertex.interfaces.frontend_build."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vertex.interfaces.frontend_build import build_frontend, strip_typescript_to_js  # noqa: E402


def build(out_dir: Path | None = None) -> list[Path]:
    return build_frontend(out_dir=out_dir, repo_root=REPO_ROOT)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build modular TypeScript frontend into static assets")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()
    out_root = args.out_dir or (REPO_ROOT / "frontend" / "dist")
    emitted = build(out_root)
    print(f"Built {len(emitted)} JS module(s) into {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
