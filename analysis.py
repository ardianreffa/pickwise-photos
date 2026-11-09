"""Pickwise Photos: per-photo analysis.

Everything here is local and uses only Pillow (plus pillow-heif for HEIC),
both of which ship with Hermes. No network, no model calls.

For each photo we measure:
  * sharpness  - "re-blur" ratio on the most detailed regions (so a sharp
                 subject on a deliberately blurred background still counts)
  * exposure   - mean brightness and the share of crushed shadows / blown
                 highlights
  * dhash      - a 64-bit difference hash used to find near-duplicates
  * taken_at   - EXIF capture time (falls back to file mtime), for bursts

RAW files are never decoded: we read the full-size JPEG preview that cameras
embed inside them, which is fast and needs no extra library.
"""

from __future__ import annotations

import io
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageFilter, ImageOps, ImageStat

try:  # HEIC/HEIF support (iPhone photos). Ships with Hermes, but stay optional.
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_OK = True
except Exception:  # pragma: no cover - depends on the host install
    HEIF_OK = False

Image.MAX_IMAGE_PIXELS = 300_000_000  # large panoramas are fine; still guards bombs

JPEG_EXT = {".jpg", ".jpeg", ".jpe"}
HEIF_EXT = {".heic", ".heif", ".hif"}
OTHER_EXT = {".png", ".tif", ".tiff", ".webp"}
RAW_EXT = {
    ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng",
    ".raf", ".orf", ".rw2", ".pef", ".srw", ".3fr", ".iiq", ".erf", ".kdc",
    ".mrw", ".x3f", ".rwl",
}
SUPPORTED_EXT = JPEG_EXT | HEIF_EXT | OTHER_EXT | RAW_EXT

MAX_FILE_BYTES = 400 * 1024 * 1024  # skip anything bigger than 400 MB
SHARP_SIZE = 1536  # long edge used for the sharpness measurement
EXPO_SIZE = 256    # long edge used for the exposure histogram
GRID = 6           # sharpness is measured on a GRID x GRID tile grid

_EXIF_DATETIME_ORIGINAL = 0x9003
_EXIF_DATETIME = 0x0132
_EXIF_IFD = 0x8769


@dataclass
class PhotoMetrics:
    path: str
    name: str
    kind: str              # jpeg | heif | raw | other
    size: int
    mtime: float
    width: int
    height: int
    taken_at: float        # epoch seconds (EXIF if available, else mtime)
    time_source: str       # exif | mtime
    sharpness: float       # 0..1, share of fine detail lost when re-blurred (1 = crisp)
    detail: float          # Laplacian energy of the most detailed regions (tie-breaker)
    brightness: float      # 0..255 mean luminance
    shadows_clipped: float # share of pixels <= 6
    highlights_clipped: float  # share of pixels >= 249
    dark_share: float      # share of pixels <= 30
    bright_share: float    # share of pixels >= 200
    dhash: str             # 16 hex chars
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def kind_of(path: Path) -> Optional[str]:
    ext = path.suffix.lower()
    if ext in JPEG_EXT:
        return "jpeg"
    if ext in HEIF_EXT:
        return "heif"
    if ext in RAW_EXT:
        return "raw"
    if ext in OTHER_EXT:
        return "other"
    return None


# ── Loading ──────────────────────────────────────────────────────────────────


def _largest_embedded_jpeg(data: bytes) -> Optional[Image.Image]:
    """Return the biggest JPEG embedded in a RAW file, or None.

    Cameras store a full-size (or near full-size) JPEG preview inside the RAW
    container. We scan for JPEG start markers, let Pillow parse the header of
    each candidate, and keep the one with the most pixels. Pillow stops at the
    JPEG end marker, so the trailing RAW data is ignored.
    """
    best: Optional[Image.Image] = None
    best_px = 0
    start = 0
    tried = 0
    while tried < 64:
        pos = data.find(b"\xff\xd8\xff", start)
        if pos < 0:
            break
        start = pos + 3
        tried += 1
        try:
            im = Image.open(io.BytesIO(data[pos:]))
            if im.format != "JPEG":
                continue
            px = im.size[0] * im.size[1]
            if px > best_px:
                best, best_px = im, px
        except Exception:
            continue
    if best is not None and best_px < 320 * 240:
        return None  # only a tiny thumbnail: not enough to judge sharpness
    return best


def open_photo(path: Path, kind: str) -> Image.Image:
    """Open a photo (or a RAW file's embedded preview), upright, in RGB/L."""
    if kind == "raw":
        with open(path, "rb") as fh:
            data = fh.read()
        im = _largest_embedded_jpeg(data)
        if im is None:
            raise ValueError("no usable JPEG preview inside this RAW file")
    else:
        if kind == "heif" and not HEIF_OK:
            raise ValueError("HEIC support (pillow-heif) is not available")
        im = Image.open(path)
    if im.format == "JPEG":
        # Let libjpeg decode at reduced size: much faster for 24-60 MP files.
        try:
            im.draft("RGB", (SHARP_SIZE * 2, SHARP_SIZE * 2))
        except Exception:
            pass
    im = ImageOps.exif_transpose(im)
    return im


def _capture_time(im: Image.Image) -> Optional[float]:
    try:
        exif = im.getexif()
    except Exception:
        return None
    raw = None
    try:
        raw = exif.get_ifd(_EXIF_IFD).get(_EXIF_DATETIME_ORIGINAL)
    except Exception:
        raw = None
    raw = raw or exif.get(_EXIF_DATETIME)
    if not raw:
        return None
    try:
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        return datetime.strptime(text.strip()[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except Exception:
        return None


# ── Measurements ─────────────────────────────────────────────────────────────

_LAPLACIAN = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


def _fit(im: Image.Image, long_edge: int) -> Image.Image:
    w, h = im.size
    scale = long_edge / max(w, h)
    if scale >= 1:
        return im.copy()
    return im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.BILINEAR)


def measure_sharpness(gray: Image.Image) -> tuple[float, float]:
    """Return (sharpness 0..1, detail energy).

    "Re-blur" test: blur the photo a little more and see how much fine detail
    disappears. A crisp photo loses most of its edge energy; a photo that is
    already blurry barely changes. The ratio does not depend on what is in the
    picture, unlike raw edge energy. It is measured on the most detailed tiles
    of a grid, so a sharp subject on a soft background still counts as sharp.
    Contrast is normalised first so dark or flat photos are not mistaken for
    blurry ones.
    """
    small = ImageOps.autocontrast(_fit(gray, SHARP_SIZE), cutoff=1)
    e1 = small.filter(_LAPLACIAN)
    e2 = small.filter(ImageFilter.GaussianBlur(1.0)).filter(_LAPLACIAN)
    w, h = small.size
    tiles = []
    for gy in range(GRID):
        for gx in range(GRID):
            box = (gx * w // GRID, gy * h // GRID, (gx + 1) * w // GRID, (gy + 1) * h // GRID)
            if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                continue
            tiles.append((ImageStat.Stat(e1.crop(box)).var[0], ImageStat.Stat(e2.crop(box)).var[0]))
    if not tiles:
        return 0.0, 0.0
    tiles.sort(reverse=True)
    top = tiles[: max(1, round(len(tiles) * 0.15))]
    v1 = sum(a for a, _ in top)
    v2 = sum(b for _, b in top)
    if v1 <= 1e-6:
        return 0.0, 0.0
    return max(0.0, 1.0 - v2 / v1), v1 / len(top)


def measure_exposure(gray: Image.Image) -> dict:
    small = _fit(gray, EXPO_SIZE)
    hist = small.histogram()
    total = float(sum(hist)) or 1.0
    return {
        "brightness": sum(i * c for i, c in enumerate(hist)) / total,
        "shadows_clipped": sum(hist[:7]) / total,
        "highlights_clipped": sum(hist[249:]) / total,
        "dark_share": sum(hist[:31]) / total,
        "bright_share": sum(hist[200:]) / total,
    }


def dhash(gray: Image.Image, size: int = 8) -> str:
    small = gray.resize((size + 1, size), Image.Resampling.BILINEAR)
    px = small.tobytes()  # mode "L": one byte per pixel
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:0{size * size // 4}x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def analyze(path: Path) -> PhotoMetrics:
    """Measure one photo. Never raises: problems are reported in `error`."""
    kind = kind_of(path) or "other"
    try:
        st = path.stat()
        size, mtime = st.st_size, st.st_mtime
    except OSError as exc:
        return _failed(path, kind, 0, 0.0, f"cannot read file: {exc.strerror or exc}")
    if size > MAX_FILE_BYTES:
        return _failed(path, kind, size, mtime, "file too large to analyze")
    try:
        im = open_photo(path, kind)
        taken = _capture_time(im)
        gray = im.convert("L")
        sharp, detail = measure_sharpness(gray)
        ex = measure_exposure(gray)
        return PhotoMetrics(
            path=str(path), name=path.name, kind=kind, size=size, mtime=mtime,
            width=im.size[0], height=im.size[1],
            taken_at=taken if taken is not None else mtime,
            time_source="exif" if taken is not None else "mtime",
            sharpness=round(sharp, 3), detail=round(detail, 1),
            brightness=round(ex["brightness"], 1), shadows_clipped=round(ex["shadows_clipped"], 4),
            highlights_clipped=round(ex["highlights_clipped"], 4),
            dark_share=round(ex["dark_share"], 4), bright_share=round(ex["bright_share"], 4),
            dhash=dhash(gray),
        )
    except Exception as exc:
        return _failed(path, kind, size, mtime, f"could not decode: {exc}")


def _failed(path: Path, kind: str, size: int, mtime: float, error: str) -> PhotoMetrics:
    return PhotoMetrics(
        path=str(path), name=path.name, kind=kind, size=size, mtime=mtime,
        width=0, height=0, taken_at=mtime, time_source="mtime", sharpness=0.0,
        detail=0.0, brightness=0.0, shadows_clipped=0.0, highlights_clipped=0.0,
        dark_share=0.0, bright_share=0.0, dhash="0" * 16, error=error,
    )


def list_photos(folder: Path, recursive: bool, limit: int) -> tuple[list[Path], int]:
    """Supported photo files in `folder` (sorted), skipping Pickwise's own output
    folder, hidden files and symlinks. Returns (paths, number skipped by limit)."""
    found: list[Path] = []
    walker = os.walk(folder) if recursive else [(str(folder), [], os.listdir(folder))]
    for root, dirs, files in walker:
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "Pickwise"]
        for fname in files:
            if fname.startswith("."):
                continue
            p = Path(root) / fname
            if p.is_symlink() or not p.is_file():
                continue
            if kind_of(p) is not None:
                found.append(p)
    found.sort(key=lambda p: str(p).lower())
    extra = max(0, len(found) - limit)
    return found[:limit], extra
