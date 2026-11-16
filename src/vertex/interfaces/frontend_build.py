"""Offline frontend build pipeline: context-aware TS type erasure into clean ES modules."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


def _repo_root() -> Path:
    # Works whether running from repo checkout (src/vertex/interfaces/) or inside Docker (/app/src/vertex/interfaces/).
    return Path(__file__).resolve().parents[3]


def _strip_inline_generics_and_casts(line: str) -> str:
    out = re.sub(r'(\bfrom\s+["\'][^"\']+)\.ts(["\'])', r"\1.js\2", line)
    out = re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)<([^<>]*(?:<[^<>]*>[^<>]*)*)>(?=\()", r"\1", out)
    as_re = re.compile(
        r"\s+as\s+(?:[A-Za-z_][A-Za-z0-9_.]*(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>)?(?:\[\])?(?:\s*\|\s*(?:null|undefined|[A-Za-z_][A-Za-z0-9_]*))*)(?=\s*(?:[;),\]\n\r]|$|\bas\b))"
    )
    prev = None
    while prev != out:
        prev = out
        out = as_re.sub("", out)
    out = re.sub(r"([A-Za-z0-9_\]\)])!(?=[.\[\n\r;,)])", r"\1", out)
    return out


def _strip_param_annotations(params_text: str) -> str:
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
            if ch == "<":
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

        if ch == "{":
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


def _strip_signature_line(line: str, head_match: re.Match[str]) -> str:
    open_idx = head_match.end() - 1
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
            return f"{head_match.group(1)}({clean_params}) {line[brace_idx:]}"
    return line


def strip_typescript_to_js(source: str) -> str:
    """Compile clean ES-module TypeScript into valid standard ES module JS without external deps."""
    lines = source.splitlines()
    filtered_lines: list[str] = []
    i = 0
    n = len(lines)
    class_depth_stack: list[int] = []
    brace_depth = 0

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

        in_class_body = bool(class_depth_stack and brace_depth == class_depth_stack[-1])
        if re.match(r"^(?:export\s+)?class\s+[A-Za-z_][A-Za-z0-9_]*", stripped) and "{" in line:
            class_depth_stack.append(brace_depth + line.count("{") - line.count("}"))
            filtered_lines.append(_strip_inline_generics_and_casts(line))
            brace_depth += line.count("{") - line.count("}")
            i += 1
            continue

        if in_class_body:
            # Class field declaration `status: number;` or `readonly code?: string;`
            if re.match(r"^(?:readonly\s+|private\s+|public\s+|protected\s+)?[A-Za-z_][A-Za-z0-9_]*\??\s*:\s*[^=;{]+;\s*$", stripped):
                i += 1
                continue
            m_method = re.match(
                r"^(\s*(?:async\s+)?[A-Za-z_][A-Za-z0-9_]*)(?:<[^>]+>)?\(",
                line,
            )
            if m_method:
                line = _strip_signature_line(line, m_method)

        m_var = re.match(
            r"^(\s*(?:export\s+)?(?:const|let|var)\s+[A-Za-z_][A-Za-z0-9_]*)\s*:\s*[^=]+=(\s*.*)$",
            line,
        )
        if m_var:
            line = f"{m_var.group(1)} = {m_var.group(2)}"

        m_fn_head = re.match(
            r"^(\s*(?:export\s+)?(?:async\s+)?function\s+[A-Za-z_][A-Za-z0-9_]*)(?:<[^>]+>)?\(",
            line,
        )
        if m_fn_head:
            line = _strip_signature_line(line, m_fn_head)
        elif not in_class_body:
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

        emitted = _strip_inline_generics_and_casts(line)
        filtered_lines.append(emitted)
        brace_depth += emitted.count("{") - emitted.count("}")
        while class_depth_stack and brace_depth < class_depth_stack[-1]:
            class_depth_stack.pop()
        i += 1

    return "\n".join(filtered_lines) + "\n"


def build_frontend(out_dir: Path | None = None, repo_root: Path | None = None) -> list[Path]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    src_root = root / "frontend" / "src"
    target_root = Path(out_dir) if out_dir is not None else (root / "frontend" / "dist")
    if target_root.exists():
        shutil.rmtree(target_root)
    assets_out = target_root / "assets"
    assets_out.mkdir(parents=True, exist_ok=True)

    emitted_js: list[Path] = []
    for ts_file in sorted(src_root.rglob("*.ts")):
        rel = ts_file.relative_to(src_root)
        target = assets_out / rel.with_suffix(".js")
        target.parent.mkdir(parents=True, exist_ok=True)
        compiled = strip_typescript_to_js(ts_file.read_text(encoding="utf-8"))
        target.write_text(compiled, encoding="utf-8")
        emitted_js.append(target)

    for css_file in sorted((src_root / "styles").glob("*.css")):
        target = assets_out / "styles" / css_file.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(css_file, target)

    shutil.copy2(src_root / "sw.js", target_root / "sw.js")
    shutil.copy2(root / "frontend" / "index.html", target_root / "index.html")
    shutil.copy2(root / "frontend" / "manifest.webmanifest", target_root / "manifest.webmanifest")

    node_bin = shutil.which("node")
    if node_bin:
        for js_file in emitted_js + [target_root / "sw.js"]:
            res = subprocess.run([node_bin, "--check", str(js_file)], capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Syntax error in compiled JS {js_file}: {res.stderr}")

    return emitted_js
