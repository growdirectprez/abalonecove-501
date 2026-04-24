#!/usr/bin/env python3
"""
link-check.py — walk the built site and verify every link resolves.

Rules:
  - Every <a href> must resolve either as a file on disk (internal)
    or as a schema'd external URL (http(s):). External URLs are NOT
    fetched — they are reported as "external (not probed)" so a
    network failure does not block the build.
  - Every <img src> must resolve to a file on disk.
  - Every relative link that starts with / is resolved against
    ~/abalonecove/.
  - Any link into ~/GrowDirect/** counts as a broken link (the
    public site cannot deep-link there per the dispatch).

Writes ~/abalonecove/build/link-report.txt and exits 0 if all
links resolve, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path("/Users/gclyle/abalonecove").resolve()
BUILD = ROOT / "build"
REPORT = BUILD / "link-report.txt"

HTML_GLOB = ["*.html", "**/*.html"]

HREF_RE = re.compile(r'<a\s+[^>]*?href\s*=\s*"([^"]+)"', re.IGNORECASE)
IMG_RE  = re.compile(r'<img\s+[^>]*?src\s*=\s*"([^"]+)"', re.IGNORECASE)
LINK_RE = re.compile(r'<link\s+[^>]*?href\s*=\s*"([^"]+)"', re.IGNORECASE)
SCRIPT_RE = re.compile(r'<script\s+[^>]*?src\s*=\s*"([^"]+)"', re.IGNORECASE)


EXCLUDE_DIR_PARTS = ("build", "node_modules", ".thumbs", "workers", ".secrets")
# Archival HTML saves from library sites and a JS-template viewer are not
# published content; they are excluded from link check.
EXCLUDE_PATTERNS = (
    "images/hdl-",                      # Huntington Digital Library offline saves
    "docs/viewer.html",                 # JS template placeholder for dynamic PDF viewer
    "images/Torrance _ San Pedro",      # library archive sidecar
    "index2.html",                      # pre-existing draft (orphaned, not linked anywhere)
    "index3.html",                      # pre-existing draft (orphaned, not linked anywhere)
)


def gather_html() -> list[Path]:
    files: set[Path] = set()
    for pat in HTML_GLOB:
        for p in ROOT.glob(pat):
            parts = p.parts
            if any(part.startswith(".") for part in parts):
                continue
            if any(skip in parts for skip in EXCLUDE_DIR_PARTS):
                continue
            rel = str(p.relative_to(ROOT))
            if any(rel.startswith(pat) or pat in rel for pat in EXCLUDE_PATTERNS):
                continue
            files.add(p.resolve())
    return sorted(files)


def resolve_internal(href: str, from_file: Path) -> Path | None:
    """Return the resolved Path if href is an internal link, else None."""
    s = href.strip()
    if not s:
        return None
    if s.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    parsed = urlparse(s)
    if parsed.scheme in ("http", "https"):
        return None
    # Strip fragment + query
    path = unquote(parsed.path)
    if not path:
        return None

    if path.startswith("/"):
        target = (ROOT / path.lstrip("/")).resolve()
    else:
        target = (from_file.parent / path).resolve()

    return target


def exists_as_page(p: Path) -> bool:
    """A link to /foo/ or /foo can succeed if p is a dir with an index.html,
    or if p is a file, or if p+.html exists."""
    if p.exists():
        if p.is_dir():
            return (p / "index.html").exists()
        return True
    if p.suffix == "" and (p.with_suffix(".html")).exists():
        return True
    return False


def check_file(p: Path, broken: list[tuple], external: list[tuple], escaped: list[tuple]) -> tuple[int, int]:
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        broken.append((p.relative_to(ROOT), "<read-error>", str(exc)))
        return (0, 0)

    a_count = 0
    i_count = 0
    for m in HREF_RE.finditer(text):
        href = m.group(1)
        a_count += 1
        parsed = urlparse(href)
        if parsed.scheme in ("http", "https"):
            external.append((p.relative_to(ROOT), href))
            continue
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        target = resolve_internal(href, p)
        if target is None:
            continue
        # detect cross-repo leak
        try:
            target.relative_to(ROOT)
        except ValueError:
            escaped.append((p.relative_to(ROOT), href, str(target)))
            continue
        if not exists_as_page(target):
            broken.append((p.relative_to(ROOT), href, str(target.relative_to(ROOT)) if target.is_relative_to(ROOT) else str(target)))

    for m in IMG_RE.finditer(text):
        src = m.group(1)
        i_count += 1
        parsed = urlparse(src)
        if parsed.scheme in ("http", "https"):
            external.append((p.relative_to(ROOT), src))
            continue
        target = resolve_internal(src, p)
        if target is None:
            continue
        try:
            target.relative_to(ROOT)
        except ValueError:
            escaped.append((p.relative_to(ROOT), src, str(target)))
            continue
        if not target.exists():
            broken.append((p.relative_to(ROOT), src, str(target.relative_to(ROOT)) if target.is_relative_to(ROOT) else str(target)))

    for pat in (LINK_RE, SCRIPT_RE):
        for m in pat.finditer(text):
            src = m.group(1)
            parsed = urlparse(src)
            if parsed.scheme in ("http", "https"):
                external.append((p.relative_to(ROOT), src))
                continue
            target = resolve_internal(src, p)
            if target is None:
                continue
            if not target.exists():
                broken.append((p.relative_to(ROOT), src, str(target.relative_to(ROOT)) if target.is_relative_to(ROOT) else str(target)))

    return (a_count, i_count)


def main() -> int:
    BUILD.mkdir(parents=True, exist_ok=True)
    broken: list[tuple] = []
    external: list[tuple] = []
    escaped: list[tuple] = []
    total_a = 0
    total_i = 0
    files = gather_html()

    for p in files:
        a, i = check_file(p, broken, external, escaped)
        total_a += a
        total_i += i

    lines = []
    lines.append("# Link report — abalonecove-site-regen")
    lines.append("")
    lines.append(f"Files scanned:       {len(files)}")
    lines.append(f"<a href> scanned:    {total_a}")
    lines.append(f"<img src> scanned:   {total_i}")
    lines.append(f"External URLs:       {len(external)} (not probed)")
    lines.append(f"Cross-repo escapes:  {len(escaped)}")
    lines.append(f"Broken internal:     {len(broken)}")
    lines.append("")

    if broken:
        lines.append("## BROKEN internal links")
        lines.append("")
        for f, h, t in broken:
            lines.append(f"  {f}")
            lines.append(f"    -> {h}")
            lines.append(f"       (resolved: {t})")
        lines.append("")

    if escaped:
        lines.append("## Cross-repo escapes (absolute-path links outside ~/abalonecove)")
        lines.append("")
        for f, h, t in escaped:
            lines.append(f"  {f}")
            lines.append(f"    -> {h}")
            lines.append(f"       (resolved: {t})")
        lines.append("")

    if external:
        # group to reduce noise
        ext_counts: dict[str, int] = {}
        for _, url in external:
            ext_counts[url] = ext_counts.get(url, 0) + 1
        lines.append("## External URLs (not probed)")
        lines.append("")
        for url, n in sorted(ext_counts.items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"  [{n}x] {url}")
        lines.append("")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"link-report: {REPORT.relative_to(ROOT)}")
    print(f"  broken:    {len(broken)}")
    print(f"  escapes:   {len(escaped)}")
    print(f"  external:  {len(external)}")

    return 1 if (broken or escaped) else 0


if __name__ == "__main__":
    sys.exit(main())
