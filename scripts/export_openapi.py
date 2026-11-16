#!/usr/bin/env python3
"""Generate frontend/src/shared/api-types.ts directly from FastAPI OpenAPI schemas."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vertex.config import Settings  # noqa: E402
from vertex.main import create_app  # noqa: E402


def _schema_to_ts(schema: dict[str, Any], indent: str = "") -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in schema:
        opts = [_schema_to_ts(opt, indent) for opt in schema["anyOf"]]
        deduped = list(dict.fromkeys(opts))
        return " | ".join(deduped)
    t = schema.get("type")
    if t == "string":
        return "string"
    if t in ("integer", "number"):
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        inner = _schema_to_ts(schema.get("items") or {}, indent)
        return f"Array<{inner}>"
    if t == "object":
        addl = schema.get("additionalProperties")
        if isinstance(addl, dict):
            return f"Record<string, {_schema_to_ts(addl, indent)}>"
        if "properties" not in schema:
            return "Record<string, unknown>"
        lines = ["{"]
        req = set(schema.get("required") or [])
        for prop, prop_schema in schema["properties"].items():
            opt_mark = "" if prop in req else "?"
            lines.append(f"{indent}  {prop}{opt_mark}: {_schema_to_ts(prop_schema, indent + '  ')};")
        lines.append(f"{indent}}}")
        return "\n".join(lines)
    return "unknown"


def generate_openapi_types() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        settings = Settings(
            data_dir=tmp_path,
            migrations_dir=REPO_ROOT / "migrations",
            frontend_dist_dir=REPO_ROOT / "frontend" / "dist",
            secret_key="openapi-export-placeholder-secret-key",
            worker_enabled=False,
        )
        app = create_app(settings)
        spec = app.openapi()

    schemas = spec.get("components", {}).get("schemas", {})
    out: list[str] = [
        "/* Auto-generated from FastAPI OpenAPI schema by scripts/export_openapi.py. Do not hand-edit. */",
        "",
    ]
    for name in sorted(schemas):
        schema = schemas[name]
        if schema.get("type") == "object" and "properties" in schema:
            out.append(f"export interface {name} {_schema_to_ts(schema)}")
            out.append("")
        else:
            out.append(f"export type {name} = {_schema_to_ts(schema)};")
            out.append("")
    return "\n".join(out)


def main() -> int:
    target = REPO_ROOT / "frontend" / "src" / "shared" / "api-types.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(generate_openapi_types(), encoding="utf-8")
    print(f"Generated {target.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
