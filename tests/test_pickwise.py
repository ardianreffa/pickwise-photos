"""Pickwise Photos tests. Run from the repo root: python -m pytest -q tests

Photos are generated on the fly with Pillow, so the tests need no fixtures.
"""

import io
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))


def _load():
    """Import the plugin package whatever the checkout folder is called."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("pickwise_photos", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pickwise_photos"] = mod
    spec.loader.exec_module(mod)
    return importlib.import_module("pickwise_photos.analysis"), importlib.import_module("pickwise_photos.engine")


analysis, engine = _load()


def scene(seed: int, size=(900, 600)) -> Image.Image:
    """A detailed synthetic 'photo': gradients, shapes and fine texture."""
    rnd = random.Random(seed)
    im = Image.new("RGB", size, (rnd.randint(60, 160),) * 3)
    d = ImageDraw.Draw(im)
    for _ in range(140):
        x, y = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        r = rnd.randint(4, 60)
        color = tuple(rnd.randint(20, 235) for _ in range(3))
        (d.ellipse if rnd.random() < 0.5 else d.rectangle)([x, y, x + r, y + r], fill=color)
    for _ in range(300):
        x, y = rnd.randint(0, size[0]), rnd.randint(0, size[1])
        d.line([x, y, x + rnd.randint(-30, 30), y + rnd.randint(-30, 30)], fill=(rnd.randint(0, 255),) * 3)
    return im


def save(im: Image.Image, path: Path, when: datetime):
    exif = Image.Exif()
    stamp = when.strftime("%Y:%m:%d %H:%M:%S")
    exif[0x0132] = stamp
    exif.get_ifd(0x8769)[0x9003] = stamp
    im.save(path, quality=92, exif=exif)


@pytest.fixture()
def folder(tmp_path):
    t0 = datetime(2026, 11, 8, 7, 0, 0)
    save(scene(1), tmp_path / "sharp_a.jpg", t0)
    save(scene(2), tmp_path / "sharp_b.jpg", t0 + timedelta(minutes=5))
    save(scene(3).filter(ImageFilter.GaussianBlur(5)), tmp_path / "blurry.jpg", t0 + timedelta(minutes=10))
    save(ImageEnhance.Brightness(scene(4)).enhance(0.1), tmp_path / "dark.jpg", t0 + timedelta(minutes=15))
    burst = scene(5)
    for i, blur in enumerate([0, 0, 2.0]):
        frame = burst.crop((i * 3, 0, 880 + i * 3, 600))
        if blur:
            frame = frame.filter(ImageFilter.GaussianBlur(blur))
        save(frame, tmp_path / f"burst_{i}.jpg", t0 + timedelta(minutes=20, seconds=i))
    (tmp_path / "broken.jpg").write_bytes(b"\xff\xd8\xff\xe0 nope")
    (tmp_path / "notes.txt").write_text("not a photo")
    return tmp_path


def metrics(folder):
    paths, _ = analysis.list_photos(folder, False, 100)
    return [analysis.analyze(p).to_dict() for p in paths]


def by_name(decisions):
    return {d["name"]: d for d in decisions}


def test_lists_only_photos(folder):
    paths, extra = analysis.list_photos(folder, False, 100)
    names = {p.name for p in paths}
    assert "notes.txt" not in names and "broken.jpg" in names and extra == 0


def test_sharp_vs_blurry(folder):
    m = {x["name"]: x for x in metrics(folder)}
    assert m["sharp_a.jpg"]["sharpness"] > 0.7
    assert m["blurry.jpg"]["sharpness"] < engine.BLUR_THRESHOLD
    assert m["dark.jpg"]["sharpness"] > engine.SOFT_THRESHOLD  # dark is not mistaken for blurry


def test_decisions_have_reasons(folder):
    d = by_name(engine.decide(metrics(folder)))
    assert d["blurry.jpg"]["decision"] == "reject" and "blurry" in d["blurry.jpg"]["reasons"][0]
    assert d["dark.jpg"]["decision"] == "reject" and "dark" in d["dark.jpg"]["reasons"][0]
    assert d["broken.jpg"]["decision"] == "reject"
    assert d["sharp_a.jpg"]["decision"] == "pick"
    assert all(x["reasons"] for x in d.values())


def test_burst_keeps_best_one(folder):
    d = by_name(engine.decide(metrics(folder)))
    burst = [d[f"burst_{i}.jpg"] for i in range(3)]
    assert len({b["group"] for b in burst}) == 1
    assert sum(b["decision"] == "pick" for b in burst) == 1
    assert d["burst_2.jpg"]["decision"] != "pick"  # the soft frame never wins


def test_brief_changes_outcome(folder):
    d = by_name(engine.decide(metrics(folder), {"keep_dark": True, "allow_soft": True}))
    assert d["dark.jpg"]["decision"] == "pick"
    assert d["blurry.jpg"]["decision"] == "maybe"


def test_count_limits_picks(folder):
    d = engine.decide(metrics(folder), {"count": 1})
    assert sum(x["decision"] == "pick" for x in d) == 1


def test_learning_from_corrections(folder):
    m = {x["name"]: x for x in metrics(folder)}
    d = by_name(engine.decide(list(m.values())))
    prefs, _ = engine.learn({}, d["dark.jpg"], "keep", m["dark.jpg"])
    prefs, change = engine.learn(prefs, d["dark.jpg"], "keep", m["dark.jpg"])
    assert "dark" in change
    again = by_name(engine.decide(list(m.values()), {}, prefs))
    assert again["dark.jpg"]["decision"] == "pick"


def test_raw_preview_is_read(tmp_path):
    full, thumb = io.BytesIO(), io.BytesIO()
    scene(9).save(full, "JPEG", quality=95)
    scene(9).resize((160, 106)).save(thumb, "JPEG")
    raw = tmp_path / "IMG_0001.NEF"
    raw.write_bytes(b"MM\x00*" + b"\x00" * 64 + thumb.getvalue() + b"\x00" * 64 + full.getvalue() + b"\x01" * 4096)
    m = analysis.analyze(raw)
    assert not m.error and m.kind == "raw" and m.width == 900 and m.sharpness > 0.7


def test_heic_is_read(tmp_path):
    if not analysis.HEIF_OK:
        pytest.skip("pillow-heif not installed")
    p = tmp_path / "phone.heic"
    scene(11).save(p)
    m = analysis.analyze(p)
    assert not m.error and m.kind == "heif"


def test_iphone_dng_uses_colour_mpo_preview_not_masks(tmp_path):
    """iPhone DNGs hold an MPO colour preview plus bigger-looking grayscale masks."""
    preview = io.BytesIO()
    scene(12).save(preview, "MPO", save_all=True, append_images=[scene(12).convert("L")])
    mask = io.BytesIO()
    Image.new("L", (1800, 1200), 230).filter(ImageFilter.GaussianBlur(3)).save(mask, "JPEG")
    raw = tmp_path / "IMG_7316.DNG"
    raw.write_bytes(b"MM\x00*" + b"\x00" * 64 + mask.getvalue() + b"\x00" * 64 + preview.getvalue())
    m = analysis.analyze(raw)
    assert not m.error and (m.width, m.height) == (900, 600)
    assert m.sharpness > 0.7 and m.brightness < 200


def test_scenery_needs_broad_focus(folder, tmp_path):
    sharp_corner = scene(13).filter(ImageFilter.GaussianBlur(6))
    sharp_corner.paste(scene(13).crop((0, 0, 220, 150)), (0, 0))
    save(sharp_corner, tmp_path / "corner.jpg", datetime(2026, 11, 8, 9, 0, 0))
    m = [analysis.analyze(tmp_path / "corner.jpg").to_dict()]
    people = engine.decide(m, {"scene": "people"})[0]
    scenery = engine.decide(m, {"scene": "scenery"})[0]
    assert people["decision"] == "pick"
    assert scenery["decision"] != "pick"
