#!/usr/bin/env python3
"""
build-gallery.py — regenerate the abalonecove.org gallery.

Scans ~/abalonecove/images/ recursively for image files, extracts
EXIF + filename metadata, writes ~/abalonecove/gallery/manifest.json,
generates ~/abalonecove/gallery/index.html, and produces thumbnails
at 400px and 1200px (max dimension) into ~/abalonecove/images/.thumbs/.

Run:

    cd ~/abalonecove
    python3 scripts/build-gallery.py

Idempotent — re-running just rebuilds. Skips thumb regeneration if the
source file's mtime is older than the existing thumb.

Dependencies: Pillow (already on system, 12.x). No piexif; Pillow's
native EXIF handling is sufficient.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from PIL import Image, ExifTags

# Allow very-large public-archive scans (Flickr-sourced historical photos
# trip Pillow's default decompression-bomb guard). This is a trusted static
# site; there is no user-supplied upload path.
Image.MAX_IMAGE_PIXELS = None

ROOT      = Path("/Users/gclyle/abalonecove").resolve()
IMG_ROOT  = ROOT / "images"
THUMB_DIR = IMG_ROOT / ".thumbs"
GALLERY   = ROOT / "gallery"
MANIFEST  = GALLERY / "manifest.json"
INDEX     = GALLERY / "index.html"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
RASTER_EXT = IMAGE_EXT - {".svg"}    # SVG has no EXIF and no useful rasterization at fixed px
MAX_UNCOMPRESSED_COMMIT = 2 * 1024 * 1024   # 2 MB

# EXIF tag id -> name
EXIF_TAGS = {v: k for k, v in ExifTags.TAGS.items()}

_FILENAME_DATE_RE = re.compile(
    r"(?P<y>18\d\d|19\d\d|20\d\d|21\d\d)"
    r"(?:[-_](?P<m>\d{1,2}))?"
    r"(?:[-_](?P<d>\d{1,2}))?"
)

_TAG_SPLIT_RE = re.compile(r"[\s\-_\.]+")
_STOP_TAGS = {
    "the", "of", "a", "an", "and", "or", "to", "in", "on", "at",
    "by", "for", "with", "from",
    "jpg", "jpeg", "png", "webp", "gif", "svg",
    "img", "image", "photo", "photograph", "scan", "copy",
}


@dataclass
class GalleryItem:
    path: str                 # relative to ROOT, e.g. "images/foo.jpg"
    filename: str
    caption: str
    date: Optional[str]       # ISO8601 (partial ok: YYYY, YYYY-MM, YYYY-MM-DD)
    tags: list[str]
    size_bytes: int
    width: Optional[int] = None
    height: Optional[int] = None
    thumb_400: Optional[str] = None   # relative path or None if SVG
    thumb_1200: Optional[str] = None


# ── Helpers ─────────────────────────────────────────────────────────

def clean_caption(stem: str) -> str:
    """Turn a filename stem into a human-readable caption."""
    # replace separators with spaces, collapse, title-case softly
    s = re.sub(r"[-_]+", " ", stem)
    s = re.sub(r"\s+", " ", s).strip()
    # keep intentional punctuation in filenames
    return s


def parse_date_from_filename(name: str) -> Optional[str]:
    """Pull a YYYY or YYYY-MM-DD date out of the filename if one is present."""
    m = _FILENAME_DATE_RE.search(name)
    if not m:
        return None
    y = m.group("y")
    mo = m.group("m")
    d = m.group("d")
    if mo and d:
        try:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except ValueError:
            return y
    if mo:
        try:
            return f"{int(y):04d}-{int(mo):02d}"
        except ValueError:
            return y
    return y


def parse_exif_date(img: Image.Image) -> Optional[str]:
    try:
        exif = img.getexif()
    except Exception:
        return None
    if not exif:
        return None
    # 36867 = DateTimeOriginal, 306 = DateTime
    for tid in (36867, 306):
        val = exif.get(tid)
        if val:
            # EXIF format: "YYYY:MM:DD HH:MM:SS"
            m = re.match(r"(\d{4}):(\d{2}):(\d{2})", val.strip())
            if m:
                return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def read_sidecar_caption(p: Path) -> Optional[str]:
    """If there's a sibling <stem>.txt next to the image, use its first line as caption."""
    sc = p.with_suffix(".txt")
    if sc.exists() and sc.is_file():
        try:
            return sc.read_text(encoding="utf-8").strip().splitlines()[0] or None
        except Exception:
            return None
    return None


def derive_tags(p: Path, rel: Path) -> list[str]:
    tags: set[str] = set()
    # parent folder tokens
    for part in rel.parts[:-1]:
        if part in (".", "images"):
            continue
        for tok in _TAG_SPLIT_RE.split(part.lower()):
            if tok and tok not in _STOP_TAGS and not tok.isdigit():
                tags.add(tok)
    # filename tokens (sans extension)
    stem = p.stem.lower()
    for tok in _TAG_SPLIT_RE.split(stem):
        if tok and tok not in _STOP_TAGS and not tok.isdigit():
            tags.add(tok)
    return sorted(tags)


def build_thumb(src: Path, dst: Path, max_dim: int) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return
    with Image.open(src) as im:
        im = im.copy()
        # normalize EXIF orientation
        try:
            exif = im.getexif()
            orientation = exif.get(0x0112) if exif else None
            if orientation:
                rotations = {3: 180, 6: 270, 8: 90}
                if orientation in rotations:
                    im = im.rotate(rotations[orientation], expand=True)
        except Exception:
            pass
        im.thumbnail((max_dim, max_dim), Image.LANCZOS)
        # JPEGs can't have alpha; normalize
        if dst.suffix.lower() in {".jpg", ".jpeg"} and im.mode in ("RGBA", "P"):
            im = im.convert("RGB")
        im.save(dst, quality=82, optimize=True)


# ── Main scan ───────────────────────────────────────────────────────

def scan() -> list[GalleryItem]:
    items: list[GalleryItem] = []
    skipped: list[str] = []
    for root, dirs, files in os.walk(IMG_ROOT):
        # skip thumbs dir and dotdirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rp = Path(root)
        for f in files:
            p = rp / f
            if p.suffix.lower() not in IMAGE_EXT:
                continue
            if p.name.startswith("."):
                continue
            try:
                rel = p.relative_to(ROOT)
            except ValueError:
                continue
            rel_img = p.relative_to(IMG_ROOT)

            width: Optional[int] = None
            height: Optional[int] = None
            exif_date: Optional[str] = None
            if p.suffix.lower() in RASTER_EXT:
                try:
                    with Image.open(p) as im:
                        width, height = im.size
                        exif_date = parse_exif_date(im)
                except Exception as exc:
                    skipped.append(f"{rel}: {exc}")

            caption = read_sidecar_caption(p) or clean_caption(p.stem)
            date = exif_date or parse_date_from_filename(p.name)
            tags = derive_tags(p, rel_img)
            size_b = p.stat().st_size

            # Thumbnails: raster only.
            thumb_400 = None
            thumb_1200 = None
            if p.suffix.lower() in RASTER_EXT:
                suffix = p.suffix.lower()
                thumb_400_path  = THUMB_DIR / "400"  / rel_img
                thumb_1200_path = THUMB_DIR / "1200" / rel_img
                try:
                    build_thumb(p, thumb_400_path, 400)
                    build_thumb(p, thumb_1200_path, 1200)
                    thumb_400  = str(thumb_400_path.relative_to(ROOT))
                    thumb_1200 = str(thumb_1200_path.relative_to(ROOT))
                except Exception as exc:
                    skipped.append(f"thumb {rel}: {exc}")

            items.append(GalleryItem(
                path=str(rel),
                filename=p.name,
                caption=caption,
                date=date,
                tags=tags,
                size_bytes=size_b,
                width=width,
                height=height,
                thumb_400=thumb_400,
                thumb_1200=thumb_1200,
            ))

    # Sort: known date first (desc), then undated by filename
    def sort_key(it: GalleryItem):
        d = it.date or ""
        # pad undated to sort last
        return (0 if d else 1, d, it.filename.lower())
    items.sort(key=sort_key)

    if skipped:
        sys.stderr.write("\n".join(skipped) + "\n")
    return items


# ── HTML render ─────────────────────────────────────────────────────

def render_index(items: list[GalleryItem]) -> str:
    # collect all tags + counts
    tag_counts: dict[str, int] = {}
    for it in items:
        for t in it.tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))[:32]

    def esc(s: str) -> str:
        return (s.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;")
                 .replace("'", "&#39;"))

    tiles = []
    for it in items:
        href = "/" + it.path
        src = "/" + (it.thumb_400 or it.path)
        big = "/" + (it.thumb_1200 or it.path)
        caption_esc = esc(it.caption)
        date_esc = esc(it.date or "")
        tags_attr = " ".join(it.tags)
        tiles.append(
            f'<a class="gallery-item" href="{esc(href)}" data-full="{esc(big)}" '
            f'data-caption="{caption_esc}" data-date="{date_esc}" '
            f'data-tags="{esc(tags_attr)}">'
            f'<img src="{esc(src)}" alt="{caption_esc}" loading="lazy">'
            f'<span class="gallery-caption">{caption_esc}</span></a>'
        )

    tag_buttons = "".join(
        f'<button data-tag="{esc(t)}">{esc(t)} <small>({n})</small></button>'
        for t, n in top_tags
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gallery — Abalone Cove</title>
  <meta name="description" content="Images of the Abalone Cove area and the Palos Verdes Peninsula — historical photos, scanned documents, maps.">
  <link rel="canonical" href="https://abalonecove.org/gallery/">
  <link rel="stylesheet" href="/css/site.css">
</head>
<body>

<header class="site-header">
  <div class="header-inner">
    <a href="/" class="wordmark"><img src="/images/shell-blended-32.png" alt="" width="28" height="28">Abalone Cove</a>
    <input type="checkbox" id="nav-toggle" class="nav-toggle" aria-label="Toggle navigation">
    <label for="nav-toggle" class="nav-toggle-label"><span></span></label>
    <ul class="nav-links">
      <li><a href="/the-story/1-the-land/">The Story</a></li>
      <li><a href="/position/">Position</a></li>
      <li><a href="/evidence/">Evidence</a></li>
      <li><a href="/map/">Map</a></li>
      <li><a href="/gallery/" aria-current="page">Gallery</a></li>
      <li><a href="/about/">About</a></li>
    </ul>
  </div>
</header>

<article class="article article-wide">
  <header class="article-header" style="text-align: left;">
    <p class="kicker">Visual archive</p>
    <h1>Gallery</h1>
    <p class="byline">{len(items)} images · {len(top_tags)} tags · regenerated from <code>images/</code></p>
  </header>

  <div class="tag-filter">
    <button data-tag="" class="active">all <small>({len(items)})</small></button>
    {tag_buttons}
  </div>

  <div class="gallery-grid" id="gallery-grid">
    {''.join(tiles)}
  </div>

  <p style="margin-top: 2.5rem; font-size: 0.85rem; color: var(--shell-sage); font-style: italic;">
    Dates derived from EXIF where available, otherwise parsed from filename.
    Captions from sibling <code>.txt</code> files where present, otherwise from
    cleaned filename. Tags from folder and filename tokens. To add context to
    an image, drop a <code>&lt;stem&gt;.txt</code> beside it in the same folder
    and re-run <code>scripts/build-gallery.py</code>.
  </p>
</article>

<footer class="site-footer">
  <div class="footer-inner">
    <p class="footer-org">Abalone Cove Foundation</p>
    <p class="footer-cta">A nonprofit, neutral-documentation project</p>
    <p class="footer-links">
      <a href="/">Home</a> ·
      <a href="/the-story/1-the-land/">The Story</a> ·
      <a href="/position/">Position</a> ·
      <a href="/evidence/">Evidence</a> ·
      <a href="/about/">About</a>
    </p>
  </div>
</footer>

<script>
(function () {{
  var grid = document.getElementById("gallery-grid");
  var buttons = document.querySelectorAll(".tag-filter button");
  buttons.forEach(function (b) {{
    b.addEventListener("click", function () {{
      buttons.forEach(function (x) {{ x.classList.remove("active"); }});
      b.classList.add("active");
      var t = b.getAttribute("data-tag") || "";
      grid.querySelectorAll(".gallery-item").forEach(function (it) {{
        var tags = (it.getAttribute("data-tags") || "").split(/\\s+/);
        it.style.display = (!t || tags.indexOf(t) !== -1) ? "" : "none";
      }});
    }});
  }});
}})();
</script>

</body>
</html>
"""


def main() -> int:
    if not IMG_ROOT.is_dir():
        print(f"no such dir: {IMG_ROOT}", file=sys.stderr)
        return 2

    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    GALLERY.mkdir(parents=True, exist_ok=True)

    items = scan()
    MANIFEST.write_text(
        json.dumps([asdict(it) for it in items], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    INDEX.write_text(render_index(items), encoding="utf-8")

    # stats
    tagged = sum(1 for it in items if it.tags)
    dated  = sum(1 for it in items if it.date)
    oversize = [it for it in items if it.size_bytes > MAX_UNCOMPRESSED_COMMIT]

    print(f"gallery: {len(items)} images")
    print(f"  tagged: {tagged}")
    print(f"  dated:  {dated}")
    print(f"  > 2MB:  {len(oversize)} (retained in repo; no R2 configured)")
    print(f"  manifest: {MANIFEST.relative_to(ROOT)}")
    print(f"  index:    {INDEX.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
