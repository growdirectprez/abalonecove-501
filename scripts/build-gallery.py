#!/usr/bin/env python3
"""
build-gallery.py — regenerate the abalonecove.org gallery.

Scans ~/abalonecove/images/ recursively for image files AND multi-page
documents (PDF, ODT), extracts EXIF + filename metadata, writes
~/abalonecove/gallery/manifest.json, generates
~/abalonecove/gallery/index.html, and produces PNG thumbnails at 400px
and 1200px into ~/abalonecove/images/.thumbs/.

For multi-page documents (.pdf, .odt), each page is rasterized via
pdftoppm and composed into a single "spine" thumbnail showing all
pages side-by-side (up to a reasonable cap).

Run:

    cd ~/abalonecove
    python3 scripts/build-gallery.py

Idempotent — re-running just rebuilds. Skips thumb regeneration if the
source file's mtime is older than the existing thumb.

External dependencies (already on the box):
  - Pillow (12.x)
  - pdftoppm (from poppler-utils; /opt/homebrew/bin/pdftoppm)
  - soffice  (from LibreOffice, for ODT→PDF; /opt/homebrew/bin/soffice)

All thumbnails are written as PNG.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
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
RASTER_EXT = IMAGE_EXT - {".svg"}
PDF_EXT = {".pdf"}
DOC_EXT = {".odt", ".doc", ".docx"}        # converted to PDF via soffice
MULTIPAGE_EXT = PDF_EXT | DOC_EXT
ALL_EXT = IMAGE_EXT | MULTIPAGE_EXT

MAX_UNCOMPRESSED_COMMIT = 2 * 1024 * 1024   # 2 MB (for log only)
SPINE_MAX_VISIBLE_PAGES = 6
SPINE_OVERLAP_RATIO = 0.30                  # each page overlaps the previous by 30%
SPINE_BG = (245, 240, 232)                  # shell-cream

PDFTOPPM = shutil.which("pdftoppm") or "/opt/homebrew/bin/pdftoppm"
SOFFICE  = shutil.which("soffice")  or "/opt/homebrew/bin/soffice"

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
    "jpg", "jpeg", "png", "webp", "gif", "svg", "pdf", "odt", "doc", "docx",
    "img", "image", "photo", "photograph", "scan", "copy",
}


@dataclass
class GalleryItem:
    kind: str                 # "image" | "multipage" | "collection"
    path: str                 # relative to ROOT: file path (image/multipage) or folder path (collection)
    filename: str             # file name (image/multipage) or folder name (collection)
    caption: str
    date: Optional[str]       # ISO8601 (partial ok: YYYY, YYYY-MM, YYYY-MM-DD)
    tags: list[str]
    size_bytes: int
    width: Optional[int] = None
    height: Optional[int] = None
    thumb_400: Optional[str] = None
    thumb_1200: Optional[str] = None
    page_count: Optional[int] = None     # multi-page only
    item_count: Optional[int] = None     # collection only: total images in folder
    href: Optional[str] = None           # override click-through URL (collections link to /.../index.html)


# ── Helpers ─────────────────────────────────────────────────────────

def clean_caption(stem: str) -> str:
    s = re.sub(r"[-_]+", " ", stem)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_date_from_filename(name: str) -> Optional[str]:
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
    for tid in (36867, 306):
        val = exif.get(tid)
        if val:
            m = re.match(r"(\d{4}):(\d{2}):(\d{2})", val.strip())
            if m:
                return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def read_sidecar_caption(p: Path) -> Optional[str]:
    sc = p.with_suffix(".txt")
    if sc.exists() and sc.is_file():
        try:
            return sc.read_text(encoding="utf-8").strip().splitlines()[0] or None
        except Exception:
            return None
    return None


def derive_tags(p: Path, rel: Path) -> list[str]:
    tags: set[str] = set()
    for part in rel.parts[:-1]:
        if part in (".", "images"):
            continue
        for tok in _TAG_SPLIT_RE.split(part.lower()):
            if tok and tok not in _STOP_TAGS and not tok.isdigit():
                tags.add(tok)
    stem = p.stem.lower()
    for tok in _TAG_SPLIT_RE.split(stem):
        if tok and tok not in _STOP_TAGS and not tok.isdigit():
            tags.add(tok)
    # Mark multi-page docs as such
    if p.suffix.lower() in PDF_EXT:
        tags.add("pdf")
        tags.add("multipage")
    elif p.suffix.lower() in DOC_EXT:
        tags.add(p.suffix.lower().lstrip("."))
        tags.add("multipage")
    return sorted(tags)


# ── Raster thumb ────────────────────────────────────────────────────

def build_raster_thumb(src: Path, dst: Path, max_dim: int) -> None:
    """Thumbnail a raster image and save as PNG."""
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
        # Save as PNG: convert palette → RGB, keep RGBA transparency
        if im.mode == "P":
            im = im.convert("RGBA")
        im.save(dst, format="PNG", optimize=True, compress_level=9)


# ── Multi-page spine ────────────────────────────────────────────────

def rasterize_pdf(pdf: Path, dpi: int, outdir: Path) -> list[Path]:
    """Rasterize every page of a PDF to PNG files in outdir, return sorted list."""
    outdir.mkdir(parents=True, exist_ok=True)
    prefix = outdir / "page"
    subprocess.run(
        [PDFTOPPM, "-r", str(dpi), "-png", str(pdf), str(prefix)],
        check=True,
        capture_output=True,
    )
    pages = sorted(outdir.glob("page-*.png"))
    if not pages:
        pages = sorted(outdir.glob("page*.png"))
    return pages


def convert_doc_to_pdf(doc: Path, outdir: Path) -> Path:
    """Convert .odt / .doc / .docx to PDF via soffice. Returns Path to PDF."""
    outdir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            SOFFICE, "--headless",
            "--convert-to", "pdf",
            "--outdir", str(outdir),
            str(doc),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # soffice writes <stem>.pdf into outdir
    pdf = outdir / f"{doc.stem}.pdf"
    if not pdf.exists():
        # Look for any .pdf in outdir as fallback
        pdfs = list(outdir.glob("*.pdf"))
        if pdfs:
            return pdfs[0]
        raise RuntimeError(f"soffice produced no PDF for {doc}: {result.stderr}")
    return pdf


def count_pages(pdf: Path) -> int:
    """Use pdfinfo to get page count, fallback to 0."""
    try:
        result = subprocess.run(
            ["/opt/homebrew/bin/pdfinfo", str(pdf)],
            capture_output=True, text=True, check=True, timeout=15,
        )
        for line in result.stdout.splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return 0


def compose_spine(page_images: list[Image.Image], max_dim: int) -> Image.Image:
    """Compose a list of PIL images into a horizontal 'spine' thumbnail.

    Pages are laid side-by-side with 30% overlap (fanned-pages effect),
    normalized to a common height. If there are more than
    SPINE_MAX_VISIBLE_PAGES, only the first SPINE_MAX_VISIBLE_PAGES are
    shown — the caller writes a page-count badge separately.

    Returns an RGB image sized to fit within max_dim × (max_dim * 2).
    The wider aspect gives the gallery tile a document-stack look.
    """
    pages = page_images[:SPINE_MAX_VISIBLE_PAGES]

    # Normalize heights
    target_h = max(im.size[1] for im in pages)
    norm: list[Image.Image] = []
    for im in pages:
        if im.size[1] != target_h:
            w = int(im.size[0] * target_h / im.size[1])
            im = im.resize((w, target_h), Image.LANCZOS)
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        norm.append(im)

    if len(norm) == 1:
        base = norm[0]
    else:
        widths = [im.size[0] for im in norm]
        # Fan: each page after the first shifts right by (1 - overlap_ratio) of its width
        strides = [int(w * (1.0 - SPINE_OVERLAP_RATIO)) for w in widths[:-1]]
        total_w = sum(strides) + widths[-1]
        canvas = Image.new("RGBA", (total_w, target_h), (0, 0, 0, 0))
        x = 0
        for i, im in enumerate(norm):
            # subtle drop shadow so overlapping pages read as separate pages
            shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
            # thin dark border on right edge for depth
            from PIL import ImageDraw
            d = ImageDraw.Draw(shadow)
            d.rectangle(
                (im.size[0] - 2, 0, im.size[0] - 1, im.size[1]),
                fill=(26, 26, 46, 90),
            )
            canvas.paste(im, (x, 0), im)
            canvas.paste(shadow, (x, 0), shadow)
            if i < len(strides):
                x += strides[i]
        base = canvas

    # Composite onto shell-cream background (PNG-legal, nice print)
    bg = Image.new("RGB", base.size, SPINE_BG)
    bg.paste(base, mask=base.split()[3])

    # Resize to fit max_dim (longest side = 2 * max_dim to allow wide spine)
    wide_cap = max_dim * 2
    bg.thumbnail((wide_cap, max_dim), Image.LANCZOS)
    return bg


def draw_page_count_badge(img: Image.Image, total_pages: int, shown_pages: int) -> Image.Image:
    """If there are more pages than shown, stamp a '+N' badge on the bottom-right."""
    from PIL import ImageDraw, ImageFont
    hidden = total_pages - shown_pages
    label = f"+{hidden} more" if hidden > 0 else f"{total_pages} pp"
    canvas = img.copy()
    draw = ImageDraw.Draw(canvas)
    # Default PIL font — deterministic across machines
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Georgia.ttf", size=max(10, img.size[1] // 22))
    except Exception:
        font = ImageFont.load_default()
    tb = draw.textbbox((0, 0), label, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = max(6, img.size[1] // 60)
    margin = max(6, img.size[1] // 40)
    x1 = canvas.size[0] - margin - tw - pad * 2
    y1 = canvas.size[1] - margin - th - pad * 2
    x2 = canvas.size[0] - margin
    y2 = canvas.size[1] - margin
    draw.rectangle((x1, y1, x2, y2), fill=(26, 26, 46, 230))
    draw.text((x1 + pad, y1 + pad - 1), label, fill=(245, 240, 232), font=font)
    return canvas


def build_multipage_thumb(src: Path, dst_400: Path, dst_1200: Path) -> int:
    """Rasterize a PDF/ODT and build spine thumbs. Returns page count."""
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)

        # Convert DOC/ODT → PDF via soffice
        if src.suffix.lower() in DOC_EXT:
            try:
                pdf = convert_doc_to_pdf(src, tmpdir / "converted")
            except Exception as exc:
                sys.stderr.write(f"spine: soffice convert failed for {src.name}: {exc}\n")
                return 0
        else:
            pdf = src

        total = count_pages(pdf)
        if total == 0:
            # pdfinfo failed; we'll still try to rasterize
            total = -1

        # Rasterize at moderate DPI for 1200px thumb
        pages_dir = tmpdir / "pages"
        try:
            page_paths = rasterize_pdf(pdf, dpi=110, outdir=pages_dir)
        except subprocess.CalledProcessError as exc:
            sys.stderr.write(f"spine: pdftoppm failed for {src.name}: {exc.stderr.decode(errors='ignore')[:200]}\n")
            return 0

        if not page_paths:
            return 0

        total = len(page_paths) if total <= 0 else total

        # Open only SPINE_MAX_VISIBLE_PAGES pages
        shown = page_paths[:SPINE_MAX_VISIBLE_PAGES]
        imgs = []
        for pp in shown:
            try:
                im = Image.open(pp)
                im.load()
                imgs.append(im)
            except Exception as exc:
                sys.stderr.write(f"spine: page open failed {pp}: {exc}\n")
        if not imgs:
            return 0

        # 1200 thumb
        big = compose_spine(imgs, 1200)
        if total > len(shown):
            big = draw_page_count_badge(big, total, len(shown))
        dst_1200.parent.mkdir(parents=True, exist_ok=True)
        big.save(dst_1200, format="PNG", optimize=True, compress_level=9)

        # 400 thumb
        small = compose_spine(imgs, 400)
        if total > len(shown):
            small = draw_page_count_badge(small, total, len(shown))
        dst_400.parent.mkdir(parents=True, exist_ok=True)
        small.save(dst_400, format="PNG", optimize=True, compress_level=9)

        return total


# ── Collection (image folder treated as multi-page document) ────────

COLLECTION_MIN = 2
COLLECTION_CAPTION_FILE = "caption.txt"


def build_collection_thumb(image_paths: list[Path], dst_400: Path, dst_1200: Path) -> None:
    """Spine thumb for an image collection — first SPINE_MAX_VISIBLE_PAGES images
    fanned horizontally, same look as the PDF spine."""
    shown = image_paths[:SPINE_MAX_VISIBLE_PAGES]
    total = len(image_paths)

    imgs: list[Image.Image] = []
    for p in shown:
        try:
            im = Image.open(p)
            im.load()
            # respect EXIF orientation
            try:
                exif = im.getexif()
                orientation = exif.get(0x0112) if exif else None
                if orientation:
                    rotations = {3: 180, 6: 270, 8: 90}
                    if orientation in rotations:
                        im = im.rotate(rotations[orientation], expand=True)
            except Exception:
                pass
            # downsize before spine so the 55-file folder doesn't allocate 500 MB
            im.thumbnail((1800, 1800), Image.LANCZOS)
            imgs.append(im)
        except Exception as exc:
            sys.stderr.write(f"collection thumb: open failed {p}: {exc}\n")
    if not imgs:
        raise RuntimeError("no usable images for collection spine")

    big = compose_spine(imgs, 1200)
    if total > len(shown):
        big = draw_page_count_badge(big, total, len(shown))
    dst_1200.parent.mkdir(parents=True, exist_ok=True)
    big.save(dst_1200, format="PNG", optimize=True, compress_level=9)

    small = compose_spine(imgs, 400)
    if total > len(shown):
        small = draw_page_count_badge(small, total, len(shown))
    dst_400.parent.mkdir(parents=True, exist_ok=True)
    small.save(dst_400, format="PNG", optimize=True, compress_level=9)


def write_collection_index(folder: Path, images: list[Path]) -> None:
    """Auto-generate <folder>/index.html listing every image.

    If <folder>/caption.txt exists, its first line is the title and the rest
    is rendered as a descriptive paragraph. Otherwise a default description
    is used.
    """
    from urllib.parse import quote

    rel = folder.relative_to(ROOT)
    href_dir = "/" + str(rel).rstrip("/") + "/"

    cap_file = folder / COLLECTION_CAPTION_FILE
    title = clean_caption(folder.name)
    description: Optional[str] = None
    if cap_file.is_file():
        try:
            txt = cap_file.read_text(encoding="utf-8").strip()
            parts = [p for p in txt.splitlines() if p.strip()]
            if parts:
                title = parts[0]
            if len(parts) > 1:
                description = " ".join(parts[1:])
        except Exception:
            pass

    def esc(s: str) -> str:
        return (s.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;"))

    total = len(images)
    tile_html = []
    for i, p in enumerate(images, 1):
        name = p.name
        href = href_dir + quote(name)
        caption = f"Page {i} of {total}"
        tile_html.append(
            f'<a class="collection-tile" href="{esc(href)}">'
            f'<img src="{esc(href)}" alt="{esc(caption)}" loading="lazy">'
            f'<span class="collection-caption">{esc(caption)}</span></a>'
        )

    # Companion files in the folder that aren't images (ODTs, PDFs, transcripts, README)
    companions = sorted(
        p for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() not in RASTER_EXT
        and p.name not in ("index.html", COLLECTION_CAPTION_FILE)
        and not p.name.startswith(".")
    )
    companion_html = ""
    if companions:
        links = []
        for p in companions:
            href = href_dir + quote(p.name)
            label = clean_caption(p.stem)
            ext = p.suffix.lstrip(".").upper()
            size_kb = max(1, p.stat().st_size // 1024)
            links.append(
                f'<li><a href="{esc(href)}">{esc(label)}</a> '
                f'<small style="color:var(--shell-warm)">({esc(ext)} · {size_kb} KB)</small></li>'
            )
        companion_html = (
            f'<section style="margin-top:2.5rem;">'
            f'<h2>Related files in this folder</h2>'
            f'<ul>{"".join(links)}</ul>'
            f'</section>'
        )

    intro = esc(description) if description else (
        f"{len(images)} source scans in this collection. Each page opens full-size in a new tab. "
        f"To edit this page's description, create a <code>caption.txt</code> in "
        f"<code>{esc(str(rel))}</code> with the title on the first line and the description below it, "
        "then re-run <code>scripts/build-gallery.py</code>."
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} — Abalone Cove</title>
  <link rel="canonical" href="https://abalonecove.org{esc(href_dir)}">
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
      <li><a href="/gallery/">Gallery</a></li>
      <li><a href="/about/">About</a></li>
    </ul>
  </div>
</header>

<article class="article article-wide">
  <header class="article-header" style="text-align: left;">
    <p class="kicker">Collection · {len(images)} scans</p>
    <h1>{esc(title)}</h1>
    <p class="byline">{intro}</p>
  </header>

  <div class="collection-grid">
    {''.join(tile_html)}
  </div>

  {companion_html}

  <p style="margin-top: 2.5rem;"><a href="/gallery/">← Back to gallery</a></p>
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

</body>
</html>
"""
    (folder / "index.html").write_text(html, encoding="utf-8")


# ── Main scan ───────────────────────────────────────────────────────

def identify_collections() -> dict[Path, list[Path]]:
    """Top-level subdirs of IMG_ROOT that contain COLLECTION_MIN+ images."""
    collections: dict[Path, list[Path]] = {}
    for child in sorted(IMG_ROOT.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        imgs = sorted(
            p for p in child.rglob("*")
            if p.is_file()
            and p.suffix.lower() in RASTER_EXT
            and not p.name.startswith(".")
        )
        if len(imgs) >= COLLECTION_MIN:
            collections[child.resolve()] = imgs
    return collections


def scan() -> list[GalleryItem]:
    items: list[GalleryItem] = []
    skipped: list[str] = []

    collections = identify_collections()

    # Pass 1 — flat items at IMG_ROOT top level (not inside a collection folder)
    for p in sorted(IMG_ROOT.iterdir()):
        if p.is_dir():
            continue
        if p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext not in ALL_EXT:
            continue

        rel = p.relative_to(ROOT)
        rel_img = p.relative_to(IMG_ROOT)

        kind = "multipage" if ext in MULTIPAGE_EXT else "image"

        width: Optional[int] = None
        height: Optional[int] = None
        exif_date: Optional[str] = None
        if ext in RASTER_EXT:
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

        thumb_400 = None
        thumb_1200 = None
        page_count = None

        thumb_stem = rel_img.with_suffix("")
        t400 = THUMB_DIR / "400" / f"{thumb_stem}.png"
        t1200 = THUMB_DIR / "1200" / f"{thumb_stem}.png"

        if kind == "image" and ext in RASTER_EXT:
            try:
                build_raster_thumb(p, t400, 400)
                build_raster_thumb(p, t1200, 1200)
                thumb_400  = str(t400.relative_to(ROOT))
                thumb_1200 = str(t1200.relative_to(ROOT))
            except Exception as exc:
                skipped.append(f"thumb {rel}: {exc}")
        elif kind == "multipage":
            def stale(t: Path) -> bool:
                return not t.exists() or t.stat().st_mtime < p.stat().st_mtime
            if stale(t400) or stale(t1200):
                try:
                    page_count = build_multipage_thumb(p, t400, t1200)
                except Exception as exc:
                    skipped.append(f"spine {rel}: {exc}")
                    page_count = 0
            else:
                page_count = count_pages(p) if ext in PDF_EXT else None
            if t400.exists():
                thumb_400 = str(t400.relative_to(ROOT))
            if t1200.exists():
                thumb_1200 = str(t1200.relative_to(ROOT))

        items.append(GalleryItem(
            kind=kind,
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
            page_count=page_count,
        ))

    # Pass 2 — one item per collection folder
    for folder, images in collections.items():
        rel_folder = folder.relative_to(ROOT)
        rel_in_img = folder.relative_to(IMG_ROOT)

        t400  = THUMB_DIR / "400"  / f"{rel_in_img}.png"
        t1200 = THUMB_DIR / "1200" / f"{rel_in_img}.png"

        # Regen thumbs if any image inside is newer than the thumb
        def stale(t: Path, imgs: list[Path]) -> bool:
            if not t.exists():
                return True
            thumb_mtime = t.stat().st_mtime
            return any(im.stat().st_mtime > thumb_mtime for im in imgs)

        try:
            if stale(t400, images) or stale(t1200, images):
                build_collection_thumb(images, t400, t1200)
        except Exception as exc:
            skipped.append(f"collection spine {rel_folder}: {exc}")

        # Always (re)write the collection's index.html — cheap and keeps
        # the listing in sync with what's on disk.
        try:
            write_collection_index(folder, images)
        except Exception as exc:
            skipped.append(f"collection index {rel_folder}: {exc}")

        caption_file = folder / COLLECTION_CAPTION_FILE
        title = clean_caption(folder.name)
        if caption_file.is_file():
            try:
                first_line = caption_file.read_text(encoding="utf-8").strip().splitlines()
                if first_line and first_line[0].strip():
                    title = first_line[0].strip()
            except Exception:
                pass

        # Collection date: EXIF of first image if available, else folder-name parse
        date: Optional[str] = parse_date_from_filename(folder.name)
        if not date and images:
            try:
                with Image.open(images[0]) as im:
                    date = parse_exif_date(im)
            except Exception:
                pass

        tags = derive_tags(folder, rel_in_img)
        tags.append("collection")
        if "multipage" not in tags:
            tags.append("multipage")
        tags = sorted(set(tags))

        href = "/" + str(rel_folder) + "/"

        size_b = sum(im.stat().st_size for im in images)

        items.append(GalleryItem(
            kind="collection",
            path=str(rel_folder),
            filename=folder.name,
            caption=title,
            date=date,
            tags=tags,
            size_bytes=size_b,
            thumb_400=str(t400.relative_to(ROOT)) if t400.exists() else None,
            thumb_1200=str(t1200.relative_to(ROOT)) if t1200.exists() else None,
            item_count=len(images),
            href=href,
        ))

    def sort_key(it: GalleryItem):
        d = it.date or ""
        return (0 if d else 1, d, it.filename.lower())
    items.sort(key=sort_key)

    if skipped:
        sys.stderr.write("\n".join(skipped) + "\n")
    return items


# ── HTML render ─────────────────────────────────────────────────────

def render_index(items: list[GalleryItem]) -> str:
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
    multipage_count = 0
    collection_count = 0
    for it in items:
        href = it.href or ("/" + it.path)
        thumb = "/" + (it.thumb_400 or it.path)
        big = "/" + (it.thumb_1200 or it.path)
        caption_esc = esc(it.caption)
        date_esc = esc(it.date or "")
        tags_attr = " ".join(it.tags)
        extra = ""
        if it.kind == "multipage":
            multipage_count += 1
            pp = f" · {it.page_count} pp" if it.page_count else ""
            extra = f'<span class="gallery-kind" title="multi-page document{pp}">DOC{pp}</span>'
        elif it.kind == "collection":
            collection_count += 1
            n = it.item_count or 0
            extra = f'<span class="gallery-kind" title="scanned document collection · {n} pages">COLLECTION · {n}</span>'
        extra_class = ""
        if it.kind == "multipage":
            extra_class = " gallery-item-multipage"
        elif it.kind == "collection":
            extra_class = " gallery-item-collection"
        tiles.append(
            f'<a class="gallery-item{extra_class}" '
            f'href="{esc(href)}" data-full="{esc(big)}" '
            f'data-caption="{caption_esc}" data-date="{date_esc}" '
            f'data-tags="{esc(tags_attr)}" data-kind="{it.kind}">'
            f'<img src="{esc(thumb)}" alt="{caption_esc}" loading="lazy">'
            f'{extra}'
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
  <meta name="description" content="Images and multi-page scans from the Abalone Cove archive — historical photos, scanned documents, maps, and multi-page PDFs rendered as page-spine thumbnails.">
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
    <p class="byline">{len(items)} items · {collection_count} collections · {multipage_count} multi-page documents · {len(top_tags)} tags · regenerated from <code>images/</code></p>
  </header>

  <div class="tag-filter">
    <button data-tag="" class="active">all <small>({len(items)})</small></button>
    {tag_buttons}
  </div>

  <div class="gallery-grid" id="gallery-grid">
    {''.join(tiles)}
  </div>

  <p style="margin-top: 2.5rem; font-size: 0.85rem; color: var(--shell-sage); font-style: italic;">
    Thumbnails rendered as PNG. For multi-page PDFs and ODT documents, each
    thumbnail is a <em>spine</em> — the first {SPINE_MAX_VISIBLE_PAGES} pages
    rasterized and fanned horizontally with an overlap, with a badge showing
    how many additional pages exist. Click through to view the full document.
    Dates derived from EXIF where available, otherwise parsed from filename.
    Captions from sibling <code>&lt;stem&gt;.txt</code> files where present,
    otherwise from cleaned filename. Tags from folder and filename tokens.
    Regenerate with <code>scripts/build-gallery.py</code>.
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

    image_items = [it for it in items if it.kind == "image"]
    multipage_items = [it for it in items if it.kind == "multipage"]
    collection_items = [it for it in items if it.kind == "collection"]
    tagged = sum(1 for it in items if it.tags)
    dated  = sum(1 for it in items if it.date)
    oversize = [it for it in items if it.size_bytes > MAX_UNCOMPRESSED_COMMIT]

    print(f"gallery: {len(items)} items")
    print(f"  images:      {len(image_items)}")
    print(f"  multipage:   {len(multipage_items)} (spined)")
    print(f"  collections: {len(collection_items)} ({sum(it.item_count or 0 for it in collection_items)} images rolled up)")
    print(f"  tagged:      {tagged}")
    print(f"  dated:       {dated}")
    print(f"  > 2MB:       {len(oversize)} (retained in repo; no R2 configured)")
    print(f"  manifest:    {MANIFEST.relative_to(ROOT)}")
    print(f"  index:       {INDEX.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
