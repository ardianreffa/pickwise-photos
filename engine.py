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
MOSTLY_BLURRY = 0.35    # below this: blurry in most of the frame -> maybe, check it
SOFT_THRESHOLD = 0.50   # below this: usable but soft

DARK_MEAN = 40.0         # mean luminance below this, with no bright area -> too dark
LOWKEY_MEAN, LOWKEY_DARK = 75.0, 0.50   # mostly dark ...
SILHOUETTE_BRIGHT = 0.03                 # ... with a bright area -> silhouette / low-key
BRIGHT_MEAN, BRIGHT_CLIPPED = 200.0, 0.25
UNDER_MEAN = 70.0         # darker than this (but not near-black): a bit underexposed
OVER_MEAN = 185.0         # brighter than this (but not blown out): a bit overexposed
PARTLY_BLOWN = 0.20      # share of pure white that makes a photo a "maybe"
WHITE_NOTE = 0.10        # above this, just mention it

BURST_GAP = 2.0          # max seconds since the PREVIOUS frame to stay in the same burst;
                         # the pause while a subject changes pose starts a new group
BURST_HASH = 12          # max dHash distance to the previous frame within a burst
NO_TIME_HASH = 6         # stricter when we cannot trust capture times

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
    blur_t, mostly_t, soft_t = BLUR_THRESHOLD * scale, MOSTLY_BLURRY * scale, SOFT_THRESHOLD * scale
    scenery = brief.get("scene") == "scenery"
    sharp = m.get("sharpness_broad", m["sharpness"]) if scenery else m["sharpness"]
    if sharp < blur_t:
        if brief.get("allow_soft"):
            maybe.append("blurry, kept as a maybe because soft or motion blur is allowed for this set")
        else:
            reject.append("blurry: no part of the photo is in focus")
    elif sharp < mostly_t:
        if not brief.get("allow_soft"):
            maybe.append("blurry in most of the frame (motion or missed focus): check it"
                         if not scenery else "most of the scene is out of focus: check it")
    elif sharp < soft_t:
        if not brief.get("allow_soft"):
            maybe.append("slightly soft: focus or camera shake" if not scenery
                         else "only a small part of the scene is sharp")

    mean, clipped = m["brightness"], m["highlights_clipped"]
    dark_share, bright_share = m.get("dark_share", 0.0), m.get("bright_share", 0.0)
    look = brief.get("look") or ""
    dark_ok = brief.get("keep_dark") or look == "low_key" or prefs.get("dark_ok", 0) >= 2
    bright_ok = brief.get("keep_bright") or look == "high_key" or prefs.get("bright_ok", 0) >= 2
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
    elif mean < UNDER_MEAN:
        # Underexposed but with detail: never thrown away (an edit can rescue it, especially
        # from RAW), never silently called "well exposed" either.
        if dark_ok:
            notes.append("darker than usual, allowed by your brief")
        else:
            maybe.append("a bit underexposed: fixable in editing, especially from RAW")
    if mean > BRIGHT_MEAN and clipped > BRIGHT_CLIPPED:
        if bright_ok:
            notes.append("very bright, allowed by your brief")
        else:
            reject.append("too bright: large areas are blown out to white")
    elif clipped > PARTLY_BLOWN and not bright_ok:
        maybe.append(f"partly blown out: {round(clipped * 100)}% of the photo is pure white")
    elif mean > OVER_MEAN:
        if bright_ok:
            notes.append("brighter than usual, allowed by your brief")
        else:
            maybe.append("a bit overexposed: check the highlights")
    elif clipped > WHITE_NOTE and not bright_ok:
        notes.append(f"{round(clipped * 100)}% of the photo is pure white (sky or highlights)")
    return reject, maybe, notes


LOOK_TARGET = {"low_key": 60.0, "high_key": 185.0}  # mean brightness each look aims for


def quality_score(m: dict, scene: str = "", look: str = "") -> float:
    """0..100. Sharpness dominates; exposure problems subtract.

    With a look preference ("low_key" or "high_key") exposure is scored against that
    look instead of a neutral mid-tone: deep shadows (or bright highlights) stop
    counting against the photo and photos closer to the look rank higher.
    """
    if m.get("error"):
        return 0.0
    s = m.get("sharpness_broad", m["sharpness"]) if scene == "scenery" else m["sharpness"]
    sharp = 55.0 * min(1.0, s / 0.85)
    sharp += 5.0 * min(1.0, math.log1p(m.get("detail", 0.0)) / math.log1p(3000.0))
    mean = m["brightness"]
    target = LOOK_TARGET.get(look, 120.0)
    exposure = 40.0 - min(40.0, abs(mean - target) / 3.0)
    if look != "low_key":
        exposure -= 40.0 * min(1.0, m["shadows_clipped"]) * 0.5
    if look != "high_key":
        exposure -= 40.0 * min(1.0, m["highlights_clipped"]) * 0.8
    else:
        exposure -= 40.0 * max(0.0, m["highlights_clipped"] - 0.35)  # only near-white frames
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
            dist = hamming(m["dhash"], last["dhash"])  # previous frame only: no chaining across poses
            if (dist <= BURST_HASH and gap <= BURST_GAP) if both_exif else dist <= NO_TIME_HASH:
                groups[-1].append(m)
                placed = True
        if not placed:
            groups.append([m])
    return groups


def _pick_reason(r: dict, brief: dict) -> str:
    look = brief.get("look") or ""
    if look == "low_key" and r["brightness"] < 90:
        return "sharp, with the low-key look you asked for"
    if look == "high_key" and r["brightness"] > 160:
        return "sharp, with the bright high-key look you asked for"
    exposure_notes = any(w in n for n in r["notes"]
                         for w in ("dark", "bright", "white", "low-key", "silhouette"))
    # Only claim "well exposed" when nothing about the exposure was noted.
    return "sharp" if exposure_notes else "sharp and well exposed"


def decide(metrics: list[dict], brief: Optional[dict] = None, prefs: Optional[dict] = None,
           overrides: Optional[dict] = None) -> list[dict]:
    """Return one decision dict per photo, in capture order."""
    brief = brief or {}
    prefs = {**DEFAULT_PREFS, **(prefs or {})}
    overrides = overrides or {}
    dedupe = brief.get("dedupe", True)
    count = brief.get("count")
    try:
        per_burst = max(1, int(brief.get("per_burst") or 1))
    except (TypeError, ValueError):
        per_burst = 1

    rows: dict[str, dict] = {}
    for gi, group in enumerate(group_bursts(metrics), start=1):
        for m in group:
            reject, maybe, notes = _flags(m, brief, prefs)
            verdict = overrides.get(m["path"])
            if verdict == "reject":
                reject, maybe, notes = ["you marked this one as a reject"], [], []
            elif verdict == "keep":
                reject, maybe, notes = [], [], []
            rows[m["path"]] = {
                "name": m["name"], "path": m["path"], "kind": m["kind"],
                "group": gi, "group_size": len(group), "score": quality_score(m, brief.get("scene", ""), brief.get("look") or ""),
                "reject": reject, "maybe": maybe, "notes": notes,
                "sharpness": m["sharpness"], "brightness": m["brightness"],
                "forced": verdict,
            }
        candidates = [rows[m["path"]] for m in group if not rows[m["path"]]["reject"]]
        if dedupe and len(candidates) > per_burst:
            ranked = sorted(candidates, key=lambda r: (r["forced"] == "keep", not r["maybe"], r["score"]),
                            reverse=True)
            kept, best = ranked[:per_burst], ranked[0]
            for r in ranked[per_burst:]:
                if r["forced"] == "keep":
                    continue
                r["maybe"].append(
                    (f"near-duplicate of {best['name']}, which scored higher "
                     f"({best['score']} vs {r['score']})" if best["score"] > r["score"] else
                     f"near-duplicate of {best['name']} (same score, {r['score']}); keeping one")
                    if per_burst == 1 else
                    f"similar burst frame; the {per_burst} best of this burst "
                    f"({', '.join(k['name'] for k in kept)}) scored higher")
                r["duplicate_of"] = best["name"]

    for r in rows.values():
        if r["forced"] == "keep":
            r["decision"], r["reasons"] = "pick", ["you marked this one as a keeper"]
        elif r["reject"]:
            r["decision"], r["reasons"] = "reject", r["reject"] + r["notes"]
        elif r["maybe"]:
            r["decision"], r["reasons"] = "maybe", r["maybe"] + r["notes"]
        else:
            r["decision"], r["reasons"] = "pick", [_pick_reason(r, brief)] + r["notes"]

    if count:
        picks = sorted((r for r in rows.values() if r["decision"] == "pick" and r["forced"] != "keep"),
                       key=lambda r: r["score"], reverse=True)
        kept = sum(1 for r in rows.values() if r["forced"] == "keep")
        for r in picks[max(0, int(count) - kept):]:
            r["decision"] = "maybe"
            r["reasons"] = [f"good, but outside your top {int(count)}"] + r["notes"]


    for r in rows.values():
        for key in ("reject", "maybe", "notes", "forced"):
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
        if "blurry" in reasons or "soft" in reasons or "out of focus" in reasons or "small part" in reasons:
            base = (BLUR_THRESHOLD if "no part" in reasons
                    else MOSTLY_BLURRY if ("most of" in reasons) else SOFT_THRESHOLD)
            target = (sharp / base) * 0.95
            # Learn gradually: one correction moves the tolerance by at most 15%.
            current = prefs["blur_scale"]
            prefs["blur_scale"] = round(max(0.3, min(current, max(current * 0.85, target))), 3)
            change = ("will be a little more tolerant of soft photos like this one"
                      if prefs["blur_scale"] < current else "remembered this photo only")
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
            change = "will be a little stricter about sharpness"
    return prefs, change
