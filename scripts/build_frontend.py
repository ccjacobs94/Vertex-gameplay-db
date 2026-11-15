#!/usr/bin/env python3
"""Offline frontend build pipeline: context-aware TS type erasure into clean ES modules."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "frontend" / "src"
DIST_ROOT = REPO_ROOT / "frontend" / "dist"


def _strip_inline_generics_and_casts(line: str) -> str:
    # Rewrite `.ts` -> `.js` on module imports.
    out = re.sub(r'(\bfrom\s+["\'][^"\']+)\.ts(["\'])', r"\1.js\2", line)
    # Remove explicit `call<Type>(` type arguments (supports nested/object generic types)
    out = re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)<([^<>]*(?:<[^<>]*>[^<>]*)*)>(?=\()", r"\1", out)
    # Remove chained or single ` as Type` casts (`as Record<string, unknown>`, `as HTMLInputElement`, `as unknown as T`)
    as_re = re.compile(
        r"\s+as\s+(?:[A-Za-z_][A-Za-z0-9_.]*(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>)?(?:\[\])?(?:\s*\|\s*(?:null|undefined|[A-Za-z_][A-Za-z0-9_]*))*)(?=\s*(?:[;),\]\n\r]|$|\bas\b))"
    )
    prev = None
    while prev != out:
        prev = out
        out = as_re.sub("", out)
    # Non-null assertions `expr!.prop` or `expr![`
    out = re.sub(r"([A-Za-z0-9_\]\)])!(?=[.\[\n\r;,)])", r"\1", out)
    return out


def _strip_param_annotations(params_text: str) -> str:
    """Erase `: Type` annotations from a parameter list (respecting defaults & nested generics/objects)."""
    parts: list[str] = []
    cur: list[str] = []
    depth_angle = 0
    depth_brace = 0
    depth_bracket = 0
    depth_paren = 0
    in_quote: str | None = None
    skipping_type = False
    i = 0

    while i < len(params_text):
        ch = params_text[i]
        if in_quote:
            if not skipping_type:
                cur.append(ch)
            if ch == "\\" and i + 1 < len(params_text):
                i += 1
                if not skipping_type:
                    cur.append(params_text[i])
            elif ch == in_quote:
                in_quote = None
            i += 1
            continue

        if ch in ('"', "'", "`"):
            in_quote = ch
            if not skipping_type:
                cur.append(ch)
            i += 1
            continue

        if skipping_type:
            if params_text[i : i + 2] == "=>":
                i += 2
                continue
            if ch in "<":
                depth_angle += 1
            elif ch == ">":
                if depth_angle > 0:
                    depth_angle -= 1
            elif ch == "{":
                depth_brace += 1
            elif ch == "}":
                depth_brace = max(0, depth_brace - 1)
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]":
                depth_bracket = max(0, depth_bracket - 1)
            elif ch == "(":
                depth_paren += 1
            elif ch == ")":
                depth_paren = max(0, depth_paren - 1)
            elif depth_angle == 0 and depth_brace == 0 and depth_bracket == 0 and depth_paren == 0:
                if ch in (",", "="):
                    skipping_type = False
                    if ch == ",":
                        parts.append("".join(cur))
                        cur = []
                    else:
                        cur.append(" =")
                i += 1
                continue
            i += 1
            continue

        if ch == "{" :
            depth_brace += 1
        elif ch == "}":
            depth_brace = max(0, depth_brace - 1)
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket = max(0, depth_bracket - 1)
        elif ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren = max(0, depth_paren - 1)
        elif ch == "?" and depth_brace == 0 and depth_bracket == 0 and depth_paren == 0:
            # Optional param `foo?: string` -> omit `?` in JS output
            i += 1
            continue
        elif ch == ":" and depth_brace == 0 and depth_bracket == 0 and depth_paren == 0:
            skipping_type = True
            i += 1
            continue
        elif ch == "," and depth_brace == 0 and depth_bracket == 0 and depth_paren == 0:
            parts.append("".join(cur))
            cur = []
            i += 1
            continue

        cur.append(ch)
        i += 1

    if cur:
        parts.append("".join(cur))
    return ", ".join(p.strip() for p in parts if p.strip())


def strip_typescript_to_js(source: str) -> str:
    """Compile clean ES-module TypeScript into valid standard ES module JS without external deps."""
    lines = source.splitlines()
    filtered_lines: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("import type "):
            while i < n and ";" not in lines[i]:
                i += 1
            i += 1
            continue

        if re.match(r"^(export\s+)?interface\s+[A-Za-z0-9_]+", stripped):
            depth = line.count("{") - line.count("}")
            i += 1
            while i < n and depth > 0:
                depth += lines[i].count("{") - lines[i].count("}")
                i += 1
            continue

        if re.match(r"^(export\s+)?type\s+[A-Za-z0-9_]+\s*=", stripped):
            depth = line.count("{") - line.count("}")
            if depth > 0:
                i += 1
                while i < n and depth > 0:
                    depth += lines[i].count("{") - lines[i].count("}")
                    i += 1
            else:
                while i < n and not lines[i].rstrip().endswith(";"):
                    i += 1
                i += 1
            continue

        # Variable annotation `const/let foo: Type = ...`
        m_var = re.match(
            r"^(\s*(?:export\s+)?(?:const|let|var)\s+[A-Za-z_][A-Za-z0-9_]*)\s*:\s*[^=]+=(\s*.*)$",
            line,
        )
        if m_var:
            line = f"{m_var.group(1)} = {m_var.group(2)}"

        # Function declaration header with balanced parentheses + optional generic + return type
        m_fn_head = re.match(
            r"^(\s*(?:export\s+)?(?:async\s+)?function\s+[A-Za-z_][A-Za-z0-9_]*)(?:<[^>]+>)?\(",
            line,
        )
        if m_fn_head:
            open_idx = m_fn_head.end() - 1
            depth = 0
            close_idx = -1
            for idx in range(open_idx, len(line)):
                ch = line[idx]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        close_idx = idx
                        break
            if close_idx != -1:
                brace_idx = line.rfind("{", close_idx + 1)
                if brace_idx != -1:
                    clean_params = _strip_param_annotations(line[open_idx + 1 : close_idx])
                    line = f"{m_fn_head.group(1)}({clean_params}) {line[brace_idx:]}"
        else:
            # Arrow callbacks with typed parameters: `(item: Foo) =>`
            m_arrow = re.search(
                r"\(([^()]*:[A-Za-z0-9_<>\[\]|.,\s\"'?]+)\)\s*(?::\s*[^{=]+)?(=>)",
                line,
            )
            if m_arrow and not line.strip().startswith(("//", "*")):
                clean_params = _strip_param_annotations(m_arrow.group(1))
                line = (
                    line[: m_arrow.start()]
                    + f"({clean_params}) {m_arrow.group(2)}"
                    + line[m_arrow.end() :]
                )

        filtered_lines.append(_strip_inline_generics_and_casts(line))
        i += 1

    return "\n".join(filtered_lines) + "\n"


def build(out_dir: Path | None = None) -> list[Path]:
    target_root = Path(out_dir) if out_dir is not None else DIST_ROOT
    if target_root.exists():
        shutil.rmtree(target_root)
    assets_out = target_root / "assets"
    assets_out.mkdir(parents=True, exist_ok=True)

    emitted_js: list[Path] = []
    for ts_file in sorted(SRC_ROOT.rglob("*.ts")):
        rel = ts_file.relative_to(SRC_ROOT)
        target = assets_out / rel.with_suffix(".js")
        target.parent.mkdir(parents=True, exist_ok=True)
        compiled = strip_typescript_to_js(ts_file.read_text(encoding="utf-8"))
        target.write_text(compiled, encoding="utf-8")
        emitted_js.append(target)

    for css_file in sorted((SRC_ROOT / "styles").glob("*.css")):
        target = assets_out / "styles" / css_file.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(css_file, target)

    shutil.copy2(SRC_ROOT / "sw.js", target_root / "sw.js")
    shutil.copy2(REPO_ROOT / "frontend" / "index.html", target_root / "index.html")
    shutil.copy2(REPO_ROOT / "frontend" / "manifest.webmanifest", target_root / "manifest.webmanifest")

    node_bin = shutil.which("node")
    if node_bin:
        for js_file in emitted_js + [target_root / "sw.js"]:
            res = subprocess.run([node_bin, "--check", str(js_file)], capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Syntax error in compiled JS {js_file}: {res.stderr}")

    return emitted_js


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build modular TypeScript frontend into static assets")
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()
    out_root = args.out_dir or DIST_ROOT
    emitted = build(out_root)
    print(f"Built {len(emitted)} JS module(s) into {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
