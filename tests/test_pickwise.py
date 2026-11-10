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


def test_pose_change_pause_starts_new_burst(tmp_path):
    """Two poses shot back to back: a pause between them must split the burst."""
    t0 = datetime(2026, 11, 8, 17, 0, 0)
    pose_a, pose_b = scene(20), scene(20).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    pose_b = pose_a.copy()
    pose_b.paste(scene(21).crop((300, 150, 600, 450)), (300, 150))  # subject moved
    for i in range(3):
        save(pose_a, tmp_path / f"a_{i}.jpg", t0 + timedelta(seconds=i))
        save(pose_b, tmp_path / f"b_{i}.jpg", t0 + timedelta(seconds=6 + i))
    d = by_name(engine.decide(metrics(tmp_path)))
    assert d["a_0.jpg"]["group"] != d["b_0.jpg"]["group"]
    assert sum(x["decision"] == "pick" for x in d.values()) == 2


def test_keep_best_n_per_burst(folder):
    one = by_name(engine.decide(metrics(folder)))
    two = by_name(engine.decide(metrics(folder), {"per_burst": 2}))
    picks = lambda d: sum(d[f"burst_{i}.jpg"]["decision"] == "pick" for i in range(3))
    assert picks(one) == 1 and picks(two) == 2
    assert two["burst_2.jpg"]["decision"] != "pick"  # the soft frame still loses


def test_learning_is_gradual():
    decision = {"decision": "maybe", "reasons": ["blurry in most of the frame (motion or missed focus): check it"]}
    prefs, change = engine.learn({}, decision, "keep", {"sharpness": 0.05, "brightness": 120})
    assert 0.84 <= prefs["blur_scale"] < 1.0 and "a little" in change


def test_rejected_burst_frame_is_replaced(folder):
    m = metrics(folder)
    first = by_name(engine.decide(m))
    winner = next(f"burst_{i}.jpg" for i in range(3) if first[f"burst_{i}.jpg"]["decision"] == "pick")
    path = first[winner]["path"]
    again = by_name(engine.decide(m, {}, {}, {path: "reject"}))
    assert again[winner]["decision"] == "reject"
    assert sum(again[f"burst_{i}.jpg"]["decision"] == "pick" for i in range(3)) == 1


def test_keep_override_survives_count(folder):
    m = metrics(folder)
    blurry = next(x["path"] for x in m if x["name"] == "blurry.jpg")
    d = by_name(engine.decide(m, {"count": 1}, {}, {blurry: "keep"}))
    assert d["blurry.jpg"]["decision"] == "pick"
    assert sum(x["decision"] == "pick" for x in d.values()) == 1


def test_review_again_without_folder(folder, tmp_path, monkeypatch):
    import importlib, json as _json
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    tools = importlib.import_module("pickwise_photos.tools")

    class State(dict):
        def get(self, k, default=None):
            return _json.loads(_json.dumps(super().get(k, default)))
        def set(self, k, v):
            self[k] = _json.loads(_json.dumps(v))

    class Ctx:
        state = State()

    pw = tools.Pickwise(Ctx())
    pw._conn = lambda: _MemDB.get()
    first = _json.loads(pw.review({"folder": str(folder), "count": 2}))
    again = _json.loads(pw.review({}))
    assert again["folder"] == first["folder"] and again["brief_applied"]["count"] == 2
    assert again["totals"]["pick"] == 2


class _MemDB:
    _db = None

    @classmethod
    def get(cls):
        import sqlite3
        if cls._db is None:
            cls._db = sqlite3.connect(":memory:", check_same_thread=False)
            cls._db.execute("CREATE TABLE metrics (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, data TEXT)")
        return cls._db


def test_apply_survives_a_failing_file(folder, tmp_path, monkeypatch):
    """One photo failing to move must not lose the record of the others (undo needs it)."""
    import importlib, json as _json, shutil as _sh
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    tools = importlib.import_module("pickwise_photos.tools")

    class State(dict):
        def get(self, k, default=None):
            return _json.loads(_json.dumps(super().get(k, default)))
        def set(self, k, v):
            self[k] = _json.loads(_json.dumps(v))

    class Ctx:
        state = State()

    pw = tools.Pickwise(Ctx())
    pw._conn = lambda: _MemDB.get()
    _json.loads(pw.review({"folder": str(folder)}))
    real_move = _sh.move

    def flaky(src, dst):
        if "sharp_b" in str(src):
            raise PermissionError(13, "Permission denied")
        return real_move(src, dst)

    monkeypatch.setattr(tools.shutil, "move", flaky)
    res = _json.loads(pw.apply({"mode": "move", "confirm": True}))
    assert res["applied"] and any("Permission denied" in s["why"] for s in res["skipped"])
    monkeypatch.setattr(tools.shutil, "move", real_move)
    undo = _json.loads(pw.undo({}))
    assert undo["restored"] == res["written"] and not undo["problems"]
    assert (folder / "sharp_a.jpg").exists() and (folder / "sharp_b.jpg").exists()


def test_bad_numbers_are_ignored(folder, tmp_path, monkeypatch):
    import importlib, json as _json
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home2"))
    tools = importlib.import_module("pickwise_photos.tools")
    assert tools._int("abc", 1, 1, 20) == 1 and tools._int("3", 1, 1, 20) == 3
    assert tools._int(None, None, 1, 10) is None and tools._int(99, 1, 1, 20) == 20


def test_low_key_look_ranks_dark_photos_first(folder):
    """A moody low-key frame (dark but detailed) wins only when the user asks for that look."""
    t = datetime(2026, 11, 8, 18, 0, 0)
    save(ImageEnhance.Brightness(scene(30)).enhance(0.5), folder / "moody.jpg", t)
    m = metrics(folder)
    plain = by_name(engine.decide(m, {"count": 1}))
    moody = by_name(engine.decide(m, {"count": 1, "look": "low_key"}))
    assert plain["moody.jpg"]["decision"] != "pick"
    assert moody["moody.jpg"]["decision"] == "pick"
    assert "low-key" in moody["moody.jpg"]["reasons"][0]
    assert moody["dark.jpg"]["decision"] != "reject"  # near-black is tolerated, not favoured


def test_reviews_live_in_sqlite_not_state(folder, tmp_path, monkeypatch):
    import importlib, json as _json
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home3"))
    tools = importlib.import_module("pickwise_photos.tools")

    class State(dict):
        def get(self, k, default=None):
            return _json.loads(_json.dumps(super().get(k, default)))
        def set(self, k, v):
            self[k] = _json.loads(_json.dumps(v))

    class Ctx:
        state = State({"plans": {"old1": {"id": "old1", "created": 1, "folder": str(folder),
                                           "brief": {}, "decisions": [], "files": {}}},
                       "last_plan": "old1"})

    pw = tools.Pickwise(Ctx())
    pw._conn = lambda: _MemDB.get()
    assert pw._get_plan(None)["id"] == "old1"          # reads a review saved by 0.1.x
    first = _json.loads(pw.review({"folder": str(folder)}))
    assert pw._get_plan(first["plan_id"])["folder"] == str(folder)
    assert not Ctx.state.get("plans")                   # migrated away, state stays small


def test_large_folder_resumes_across_calls(folder, tmp_path, monkeypatch):
    """With a tiny time budget the review stops early, keeps its work and resumes."""
    import importlib, json as _json
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home4"))
    tools = importlib.import_module("pickwise_photos.tools")
    monkeypatch.setattr(tools, "TIME_BUDGET", 0.0)
    monkeypatch.setattr(tools, "CHUNK", 2)

    class State(dict):
        def get(self, k, default=None):
            return _json.loads(_json.dumps(super().get(k, default)))
        def set(self, k, v):
            self[k] = _json.loads(_json.dumps(v))

    class Ctx:
        state = State()

    _MemDB._db = None
    pw = tools.Pickwise(Ctx())
    pw._conn = lambda: _MemDB.get()
    r = _json.loads(pw.review({"folder": str(folder), "count": 2}))
    calls = 1
    while r.get("in_progress"):
        assert r["analysed"] < r["photos"]
        r = _json.loads(pw.review({}))  # continue without repeating the folder
        calls += 1
    assert calls > 2 and r["totals"]["pick"] == 2 and r["brief_applied"]["count"] == 2


def test_copy_is_refused_when_disk_is_too_full(folder, tmp_path, monkeypatch):
    import importlib, json as _json
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home5"))
    tools = importlib.import_module("pickwise_photos.tools")

    class State(dict):
        def get(self, k, default=None):
            return _json.loads(_json.dumps(super().get(k, default)))
        def set(self, k, v):
            self[k] = _json.loads(_json.dumps(v))

    class Ctx:
        state = State()

    _MemDB._db = None
    pw = tools.Pickwise(Ctx())
    pw._conn = lambda: _MemDB.get()
    pw.review({"folder": str(folder)})
    assert "copy_needs" in _json.loads(pw.apply({"mode": "copy"}))["disk"]
    monkeypatch.setattr(tools, "SPACE_MARGIN", 10 ** 18)
    assert "warning" in _json.loads(pw.apply({"mode": "copy"}))
    refused = _json.loads(pw.apply({"mode": "copy", "confirm": True}))
    assert "not enough free disk space" in refused["error"]
    assert not (folder / "Pickwise").exists()


def test_raw_jpeg_pairs_travel_together(tmp_path, monkeypatch):
    """Sony-style RAW+JPEG: one photo, judged on the JPEG, both files in the same pile."""
    import importlib, json as _json
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home6"))
    tools = importlib.import_module("pickwise_photos.tools")
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    t0 = datetime(2026, 11, 8, 7, 0, 0)
    for i, im in enumerate([scene(40), scene(41).filter(ImageFilter.GaussianBlur(6))]):
        save(im, shoot / f"DSC0000{i}.JPG", t0 + timedelta(minutes=i))
        preview = io.BytesIO()
        im.resize((450, 300)).save(preview, "JPEG")
        (shoot / f"DSC0000{i}.ARW").write_bytes(b"II*\x00" + b"\x00" * 64 + preview.getvalue())

    class State(dict):
        def get(self, k, default=None):
            return _json.loads(_json.dumps(super().get(k, default)))
        def set(self, k, v):
            self[k] = _json.loads(_json.dumps(v))

    class Ctx:
        state = State()

    _MemDB._db = None
    pw = tools.Pickwise(Ctx())
    pw._conn = lambda: _MemDB.get()
    r = _json.loads(pw.review({"folder": str(shoot)}))
    assert r["photos"] == 2 and r["raw_jpeg_pairs"] == 2
    assert r["picks"][0]["name"] == "DSC00000.JPG" and r["picks"][0]["paired_with"] == ["DSC00000.ARW"]
    fb = _json.loads(pw.feedback({"photo": "DSC00001.ARW", "verdict": "keep"}))  # RAW name works too
    assert fb["photo"] == "DSC00001.JPG"
    pw.review({})
    moved = _json.loads(pw.apply({"mode": "move", "confirm": True}))
    assert moved["written"] == 4
    picks = shoot / "Pickwise" / "Picks"
    assert {p.name for p in picks.iterdir()} == {"DSC00000.JPG", "DSC00000.ARW", "DSC00001.JPG", "DSC00001.ARW"}
    undo = _json.loads(pw.undo({}))
    assert undo["restored"] == 4 and (shoot / "DSC00001.ARW").exists()


def test_underexposed_is_maybe_and_never_called_well_exposed(folder):
    t = datetime(2026, 11, 8, 19, 0, 0)
    save(ImageEnhance.Brightness(scene(50)).enhance(0.45), folder / "under.jpg", t)
    m = metrics(folder)
    d = by_name(engine.decide(m))
    under = d["under.jpg"]
    assert under["decision"] == "maybe" and "underexposed" in under["reasons"][0]
    ok = by_name(engine.decide(m, {"keep_dark": True}))["under.jpg"]
    assert ok["decision"] == "pick" and "well exposed" not in ok["reasons"][0]
