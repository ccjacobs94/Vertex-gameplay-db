"""Module entry point for `python -m vertex`."""

from __future__ import annotations

import sys

from vertex.interfaces.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
