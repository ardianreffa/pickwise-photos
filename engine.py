"""Pickwise Photos: turn measurements + a brief into picks, with reasons.

Pure functions over plain dicts, so they are easy to test and explain.
Decisions are one of: "pick", "maybe", "reject". Every photo gets at least
one human-readable reason.
"""

from __future__ import annotations

import math
from typing import Optional

from .analysis import hamming

# Thresholds on the 0..1 "re-blur" sharpness ratio from analysis.measure_sharpness
# (crisp photos score about 0.85-0.97, soft ones 0.05-0.3, blurry ones near 0).
# The user's corrections scale them through prefs["blur_scale"].
BLUR_THRESHOLD = 0.20   # below this: nothing in the photo is in focus -> blurry
SOFT_THRESHOLD = 0.50   # below this: usable but soft

DARK_MEAN = 40.0         # mean luminance below this, with no bright area -> too dark
LOWKEY_MEAN, LOWKEY_DARK = 75.0, 0.50   # mostly dark ...
SILHOUETTE_BRIGHT = 0.03                 # ... with a bright area -> silhouette / low-key
BRIGHT_MEAN, BRIGHT_CLIPPED = 200.0, 0.25
PARTLY_BLOWN = 0.12

BURST_SECONDS = 4.0      # photos closer than this in time may be one burst
BURST_HASH = 12          # max dHash distance for photos in the same burst
NO_TIME_HASH = 8         # stricter when we cannot trust capture times

DEFAULT_PREFS = {"blur_scale": 1.0, "dark_ok": 0.0, "bright_ok": 0.0}


def _flags(m: dict, brief: dict, prefs: dict) -> tuple[list[str], list[str], list[str]]:
    """Return (reject_reasons, maybe_reasons, notes) for one photo."""
    reject, maybe, notes = [], [], []
    if m.get("error"):
        return [f"could not be read ({m['error']})"], [], []

    scale = max(0.3, min(2.0, float(prefs.get("blur_scale", 1.0))))
    if brief.get("strictness") == "relaxed":
        scale *= 0.75
    elif brief.get("strictness") == "strict":
        scale *= 1.3
    blur_t, soft_t = BLUR_THRESHOLD * scale, SOFT_THRESHOLD * scale
    sharp = m["sharpness"]
    if sharp < blur_t:
        if brief.get("allow_soft"):
            maybe.append("blurry, kept as a maybe because soft or motion blur is allowed for this set")
        else:
            reject.append("blurry: no part of the photo is in focus")
    elif sharp < soft_t:
        if not brief.get("allow_soft"):
            maybe.append("slightly soft: focus or camera shake")

    mean, clipped = m["brightness"], m["highlights_clipped"]
    dark_share, bright_share = m.get("dark_share", 0.0), m.get("bright_share", 0.0)
    dark_ok = brief.get("keep_dark") or prefs.get("dark_ok", 0) >= 2
    bright_ok = brief.get("keep_bright") or prefs.get("bright_ok", 0) >= 2
    if mean < LOWKEY_MEAN and dark_share > LOWKEY_DARK and bright_share >= SILHOUETTE_BRIGHT:
        if dark_ok:
            notes.append("low-key or silhouette, allowed by your brief")
        else:
            maybe.append("mostly dark with a bright area: could be an intentional silhouette")
    elif mean < DARK_MEAN:
        if dark_ok:
            notes.append("dark, allowed by your brief")
        else:
            reject.append("too dark: most of the photo is close to black")
    if mean > BRIGHT_MEAN and clipped > BRIGHT_CLIPPED:
        if bright_ok:
            notes.append("very bright, allowed by your brief")
        else:
            reject.append("too bright: large areas are blown out to white")
    elif clipped > PARTLY_BLOWN and not bright_ok:
        maybe.append(f"partly blown out: {round(clipped * 100)}% of the photo is pure white")
    return reject, maybe, notes


def quality_score(m: dict) -> float:
    """0..100. Sharpness dominates; exposure problems subtract."""
    if m.get("error"):
        return 0.0
    sharp = 55.0 * min(1.0, m["sharpness"] / 0.85)
    sharp += 5.0 * min(1.0, math.log1p(m.get("detail", 0.0)) / math.log1p(3000.0))
    mean = m["brightness"]
    exposure = 40.0 - min(40.0, abs(mean - 120.0) / 3.0)
    exposure -= 40.0 * min(1.0, m["shadows_clipped"]) * 0.5
    exposure -= 40.0 * min(1.0, m["highlights_clipped"]) * 0.8
    return round(max(0.0, sharp + max(0.0, exposure)), 1)


def group_bursts(metrics: list[dict]) -> list[list[dict]]:
    """Group consecutive near-identical photos (bursts / repeated frames)."""
    ordered = sorted(metrics, key=lambda m: (m["taken_at"], m["name"].lower()))
    groups: list[list[dict]] = []
    for m in ordered:
        if m.get("error"):
            groups.append([m])
            continue
        placed = False
        if groups and not groups[-1][0].get("error"):
            last = groups[-1][-1]
            both_exif = m["time_source"] == "exif" and last["time_source"] == "exif"
            gap = abs(m["taken_at"] - last["taken_at"])
            dist = min(hamming(m["dhash"], g["dhash"]) for g in groups[-1])
            if (both_exif and gap <= BURST_SECONDS and dist <= BURST_HASH) or dist <= NO_TIME_HASH:
                groups[-1].append(m)
                placed = True
        if not placed:
            groups.append([m])
    return groups


def decide(metrics: list[dict], brief: Optional[dict] = None, prefs: Optional[dict] = None,
           overrides: Optional[dict] = None) -> list[dict]:
    """Return one decision dict per photo, in capture order."""
    brief = brief or {}
    prefs = {**DEFAULT_PREFS, **(prefs or {})}
    overrides = overrides or {}
    dedupe = brief.get("dedupe", True)
    count = brief.get("count")

    rows: dict[str, dict] = {}
    for gi, group in enumerate(group_bursts(metrics), start=1):
        for m in group:
            reject, maybe, notes = _flags(m, brief, prefs)
            rows[m["path"]] = {
                "name": m["name"], "path": m["path"], "kind": m["kind"],
                "group": gi, "group_size": len(group), "score": quality_score(m),
                "reject": reject, "maybe": maybe, "notes": notes,
                "sharpness": m["sharpness"], "brightness": m["brightness"],
            }
        candidates = [rows[m["path"]] for m in group if not rows[m["path"]]["reject"]]
        if dedupe and len(candidates) > 1:
            best = max(candidates, key=lambda r: (not r["maybe"], r["score"]))
            for r in candidates:
                if r is not best:
                    r["maybe"].append(
                        f"near-duplicate of {best['name']}, which scored higher "
                        f"({best['score']} vs {r['score']})")
                    r["duplicate_of"] = best["name"]

    for r in rows.values():
        if r["reject"]:
            r["decision"], r["reasons"] = "reject", r["reject"] + r["notes"]
        elif r["maybe"]:
            r["decision"], r["reasons"] = "maybe", r["maybe"] + r["notes"]
        else:
            r["decision"], r["reasons"] = "pick", ["sharp and well exposed"] + r["notes"]

    if count:
        picks = sorted((r for r in rows.values() if r["decision"] == "pick"),
                       key=lambda r: r["score"], reverse=True)
        for r in picks[int(count):]:
            r["decision"] = "maybe"
            r["reasons"] = [f"good, but outside your top {int(count)}"] + r["notes"]

    for path, verdict in overrides.items():
        r = rows.get(path)
        if r is None:
            continue
        if verdict == "keep":
            r["decision"], r["reasons"] = "pick", ["you marked this one as a keeper"]
        elif verdict == "reject":
            r["decision"], r["reasons"] = "reject", ["you marked this one as a reject"]

    for r in rows.values():
        for key in ("reject", "maybe", "notes"):
            r.pop(key, None)
    order = {m["path"]: i for i, m in enumerate(sorted(metrics, key=lambda m: (m["taken_at"], m["name"].lower())))}
    return sorted(rows.values(), key=lambda r: order[r["path"]])


def learn(prefs: dict, decision: dict, verdict: str, metrics: dict) -> tuple[dict, str]:
    """Adjust preferences from one correction. Returns (new prefs, what changed)."""
    prefs = {**DEFAULT_PREFS, **(prefs or {})}
    sharp = metrics.get("sharpness", 0.0)
    dark = metrics.get("brightness", 128.0) < LOWKEY_MEAN
    bright = metrics.get("brightness", 128.0) > BRIGHT_MEAN
    reasons = " ".join(decision.get("reasons", []))
    change = "remembered this photo only"
    if verdict == "keep" and decision.get("decision") != "pick":
        if "blurry" in reasons or "soft" in reasons:
            base = BLUR_THRESHOLD if "blurry" in reasons else SOFT_THRESHOLD
            target = (sharp / base) * 0.95
            prefs["blur_scale"] = round(max(0.3, min(prefs["blur_scale"] * 0.85, target)), 3)
            change = "will be more tolerant of soft photos like this one"
        elif dark or "dark" in reasons or "silhouette" in reasons:
            prefs["dark_ok"] = prefs.get("dark_ok", 0) + 1
            change = ("will keep dark and silhouette shots from now on" if prefs["dark_ok"] >= 2
                      else "noted you like dark shots; one more and they stay by default")
        elif bright or "bright" in reasons:
            prefs["bright_ok"] = prefs.get("bright_ok", 0) + 1
            change = ("will keep bright, high-key shots from now on" if prefs["bright_ok"] >= 2
                      else "noted you like bright shots; one more and they stay by default")
    elif verdict == "reject" and decision.get("decision") == "pick":
        if sharp < SOFT_THRESHOLD * prefs["blur_scale"] * 1.5:
            prefs["blur_scale"] = round(min(2.0, prefs["blur_scale"] * 1.15), 3)
            change = "will be stricter about sharpness"
    return prefs, change
