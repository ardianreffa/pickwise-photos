"""Pickwise Photos: tool handlers.

Safety rules this file follows:
  * Original photos are never deleted, overwritten, or edited.
  * Nothing is written until `pickwise_apply` is called with confirm=true;
    without it the call is a dry run that only describes the plan.
  * "move" is reversible with `pickwise_undo`; "copy" leaves originals alone;
    "xmp" only adds rating sidecar files and never replaces an existing one.
  * No network access, no model calls, no shell commands.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from . import analysis, engine

PLUGIN = "pickwise-photos"
OUT_DIR = "Pickwise"
BUCKETS = {"pick": "Picks", "maybe": "Maybe", "reject": "Rejects"}
XMP_RATING = {"pick": ("5", "Green"), "maybe": ("3", "Yellow"), "reject": ("-1", "Red")}
MAX_PHOTOS_DEFAULT = 10000
TIME_BUDGET = 240.0      # seconds of analysis per call; Hermes stops a tool after 420 s
CHUNK = 48               # photos analysed (and saved) per step
PREVIEW_EDGE = 1568      # long edge of preview JPEGs: what vision models actually look at
PREVIEW_LIMIT = 40
KEEP_PREVIEW_SETS = 3
LIST_LIMIT = 40
KEEP_PLANS = 10
KEEP_REPORTS = 20
SPACE_MARGIN = 1024 ** 3  # keep at least 1 GB free after copying


def _ok(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _err(message: str, **extra: Any) -> str:
    return json.dumps({"error": message, **extra}, ensure_ascii=False)


def _session_dir() -> Path:
    try:
        from agent.runtime_cwd import resolve_agent_cwd

        return Path(resolve_agent_cwd())
    except Exception:
        return Path.home()


def _int(value: Any, default: Optional[int], low: int, high: int) -> Optional[int]:
    """Lenient int parsing for model-supplied arguments: bad values fall back to default."""
    try:
        if value is None or value == "":
            return default
        return max(low, min(high, int(float(value))))
    except (TypeError, ValueError):
        return default


def _workers() -> int:
    """Photos decoded in parallel, sized to the machine: a 48 MP HEIC needs a few hundred
    MB while it is open, so an 8 GB laptop gets 2 workers and a 24 GB one up to 8."""
    cpus = os.cpu_count() or 4
    try:
        ram_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except (ValueError, OSError, AttributeError):
        ram_gb = 8.0
    return max(2, min(8, cpus, int(ram_gb // 3)))


def _stat_map(paths) -> dict:
    out = {}
    for p in paths:
        try:
            st = os.stat(p)
            out[str(p)] = [st.st_size, st.st_mtime]
        except OSError:
            pass
    return out


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def resolve_folder(raw: str) -> Path:
    if not raw or not str(raw).strip():
        raise ValueError("no folder given")
    p = Path(os.path.expanduser(str(raw).strip()))
    if not p.is_absolute():
        p = _session_dir() / p
    p = p.resolve()
    if not p.exists():
        raise ValueError(f"folder not found: {p}")
    if not p.is_dir():
        raise ValueError(f"not a folder: {p}")
    return p


class Pickwise:
    """State + handlers. One instance per plugin load."""

    def __init__(self, ctx: Any = None):
        self.ctx = ctx
        self._db = None

    # ── storage ─────────────────────────────────────────────────────────────

    def _conn(self):
        if self._db is None:
            from plugins.plugin_storage import plugin_db

            self._db = plugin_db(PLUGIN)
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS metrics (path TEXT PRIMARY KEY, size INTEGER, "
                "mtime REAL, data TEXT)")
            self._db.commit()
        return self._db

    def _state_get(self, key: str, default: Any) -> Any:
        try:
            value = self.ctx.state.get(key, default=default)
            return default if value is None else value
        except Exception:
            return default

    def _state_set(self, key: str, value: Any) -> None:
        self.ctx.state.set(key, value)

    def _reports_dir(self) -> Path:
        from plugins.plugin_storage import plugin_data_dir

        d = plugin_data_dir(PLUGIN) / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── analysis with cache ─────────────────────────────────────────────────

    def _metrics_for(self, paths: list[Path], budget: Optional[float] = None) -> tuple[list[dict], int]:
        """Measure photos, using the cache. Returns (metrics, number still to analyse).

        Work is saved after every chunk and stops once the time budget is spent, so a
        folder of thousands of photos finishes over a few calls instead of hitting
        Hermes' per-tool deadline and losing everything.
        """
        conn = self._conn()
        cached: dict[str, dict] = {}
        todo: list[Path] = []
        for p in paths:
            try:
                st = p.stat()
            except OSError:
                todo.append(p)
                continue
            row = conn.execute("SELECT size, mtime, data FROM metrics WHERE path = ?", (str(p),)).fetchone()
            data = json.loads(row[2]) if row else None
            if (data and row[0] == st.st_size and abs(row[1] - st.st_mtime) < 1e-6
                    and data.get("v") == analysis.ANALYSIS_VERSION):
                cached[str(p)] = data
            else:
                todo.append(p)
        started = time.time()
        remaining = len(todo)
        if todo:
            with ThreadPoolExecutor(max_workers=_workers()) as pool:
                for i in range(0, len(todo), CHUNK):
                    chunk = todo[i:i + CHUNK]
                    for m in pool.map(analysis.analyze, chunk):
                        d = {**m.to_dict(), "v": analysis.ANALYSIS_VERSION}
                        cached[m.path] = d
                        if not m.error:
                            conn.execute("INSERT OR REPLACE INTO metrics(path, size, mtime, data) "
                                         "VALUES (?,?,?,?)", (m.path, m.size, m.mtime, json.dumps(d)))
                    conn.commit()
                    remaining -= len(chunk)
                    if budget is not None and remaining and time.time() - started > budget:
                        break
        return [cached[str(p)] for p in paths if str(p) in cached], remaining

    # ── tools ───────────────────────────────────────────────────────────────

    def review(self, args: dict, **_: Any) -> str:
        last = None
        if not str(args.get("folder") or "").strip():
            pending = self._state_get("pending_review", None)
            if pending:  # continue an unfinished review of a large folder
                return self.review({**pending, **{k: v for k, v in args.items() if v is not None}})
            # "Review again": reuse the latest review's folder and brief, so a fresh
            # session (or a review after feedback) does not need them repeated.
            last = self._get_plan(None)
            if last is None:
                return _err("which folder? give the folder with the photos")
            args = {**{k: v for k, v in last.get("brief", {}).items()
                       if k != "scene" or v in ("people", "scenery")},
                    **{k: v for k, v in args.items() if v is not None}, "folder": last["folder"]}
        try:
            folder = resolve_folder(args.get("folder", ""))
        except ValueError as exc:
            return _err(str(exc))
        try:
            limit = _int(args.get("max_photos"), MAX_PHOTOS_DEFAULT, 1, 10000)
            paths, skipped = analysis.list_photos(folder, bool(args.get("recursive")), limit)
            if not paths:
                return _err("no supported photos in this folder",
                            folder=str(folder), supported=sorted(analysis.SUPPORTED_EXT))
            started = time.time()
            all_files = paths
            paths, companions = analysis.pair_files(paths)
            metrics, remaining = self._metrics_for(paths, TIME_BUDGET)
            if remaining:
                self._state_set("pending_review", {k: v for k, v in args.items() if k != "plan_id"}
                                | {"folder": str(folder)})
                return _ok({
                    "in_progress": True, "folder": str(folder), "photos": len(paths),
                    "analysed": len(paths) - remaining, "remaining": remaining,
                    "seconds": round(time.time() - started, 1),
                    "next_step": ("Large folder: analysis is saved as it goes. Tell the user how far "
                                  "it got, then call pickwise_review again (same arguments, or no "
                                  "folder) to continue from where it stopped. Nothing was written."),
                })
            self._state_set("pending_review", None)
            brief = {
                "count": _int(args.get("count"), None, 1, 100000),
                "keep_dark": bool(args.get("keep_dark")),
                "keep_bright": bool(args.get("keep_bright")),
                "allow_soft": bool(args.get("allow_soft")),
                "dedupe": args.get("dedupe", True) is not False,
                "per_burst": _int(args.get("per_burst"), 1, 1, 20),
                "strictness": args.get("strictness") or "normal",
                "scene": args.get("scene") if args.get("scene") in ("people", "scenery") else "auto",
                "look": args.get("look") if args.get("look") in ("low_key", "high_key") else None,
            }
            prefs = self._state_get("prefs", dict(engine.DEFAULT_PREFS))
            overrides = self._state_get("overrides", {})
            decisions = engine.decide(metrics, brief, prefs, overrides)
            plan_id = uuid.uuid4().hex[:8]
            plan = {
                "id": plan_id, "folder": str(folder), "created": time.time(), "brief": brief,
                "brief_note": str(args.get("brief_note") or "")[:500],
                "decisions": [{**{k: d[k] for k in ("name", "path", "decision", "reasons", "score", "group")},
                               **({"companions": companions[d["path"]]} if d["path"] in companions else {})}
                              for d in decisions],
                "files": {**{m["path"]: [m["size"], m["mtime"]] for m in metrics},
                          **_stat_map(c for cs in companions.values() for c in cs)},
                "applied": None,
            }
            self._save_plan(plan)
            report = self._write_report(plan, metrics)
            return _ok(self._summary(plan, decisions, metrics, prefs, overrides, skipped,
                                     round(time.time() - started, 1), report))
        except Exception as exc:  # never raise into the agent loop
            return _err(f"review failed: {exc}")

    def apply(self, args: dict, **_: Any) -> str:
        try:
            plan = self._get_plan(args.get("plan_id"))
            if plan is None:
                return _err("no review found; run pickwise_review first")
            mode = (args.get("mode") or "copy").lower()
            if mode not in ("copy", "move", "xmp"):
                return _err("mode must be copy, move or xmp")
            folder = Path(plan["folder"])
            out = folder / OUT_DIR
            counts = {k: 0 for k in BUCKETS}
            for d in plan["decisions"]:
                counts[d["decision"]] += 1
            needed = sum(v[0] for v in plan["files"].values()) if mode == "copy" else 0
            try:
                free = shutil.disk_usage(folder).free
            except OSError:
                free = None
            fits = free is None or needed + SPACE_MARGIN <= free
            space = {"copy_needs": _human(needed), "free_space": _human(free) if free is not None else "unknown"}
            if not args.get("confirm"):
                where = (f"subfolders Picks/Maybe/Rejects inside {out}" if mode != "xmp"
                         else "a .xmp rating sidecar next to each photo (existing sidecars are left alone)")
                return _ok({
                    "dry_run": True, "plan_id": plan["id"], "mode": mode, "counts": counts,
                    "would_write": where,
                    "originals": ("moved (undo with pickwise_undo)" if mode == "move"
                                  else "left untouched"),
                    **({"disk": space} if mode == "copy" else {}),
                    **({} if fits else {"warning": "Not enough free disk space for copies. Suggest "
                                                   "'move' (no extra space, undoable) or 'xmp'."}),
                    "next_step": "Show this to the user. Only call again with confirm=true after "
                                 "they explicitly agree.",
                })
            if not fits:
                return _err("not enough free disk space to copy these photos",
                            disk=space, suggestion="use mode 'move' (undoable) or 'xmp' instead")
            if plan.get("applied"):
                return _err(f"this review was already applied ({plan['applied']['mode']}); "
                            "run a new review to apply again")
            done, skipped = [], []
            for d in plan["decisions"]:
                for path in [d["path"]] + d.get("companions", []):
                    self._apply_one(Path(path), d["decision"], plan, mode, out, done, skipped)
            plan["applied"] = {"mode": mode, "at": time.time(), "done": done}
            self._save_plan(plan)
            return _ok({
                "applied": True, "plan_id": plan["id"], "mode": mode, "counts": counts,
                "written": len(done), "skipped": skipped[:LIST_LIMIT], "skipped_total": len(skipped),
                "output": str(out) if mode != "xmp" else str(folder),
                "undo": "pickwise_undo moves the files back" if mode == "move" else None,
            })
        except Exception as exc:
            return _err(f"apply failed: {exc}")

    def _apply_one(self, src: Path, decision: str, plan: dict, mode: str, out: Path,
                   done: list, skipped: list) -> None:
        """Copy, move or rate one file. Errors are recorded, never raised: a failure on
        one file must not lose track of files already moved (undo depends on it)."""
        size_mtime = plan["files"].get(str(src))
        try:
            st = src.stat()
        except OSError:
            skipped.append({"name": src.name, "why": "file no longer exists"})
            return
        if size_mtime and (st.st_size != size_mtime[0] or abs(st.st_mtime - size_mtime[1]) > 1e-6):
            skipped.append({"name": src.name, "why": "file changed since the review"})
            return
        try:
            if mode == "xmp":
                side = (src.with_suffix(".xmp") if analysis.kind_of(src) == "raw"
                        else src.with_name(src.name + ".xmp"))
                if side.exists():
                    skipped.append({"name": src.name, "why": f"{side.name} already exists"})
                    return
                rating, label = XMP_RATING[decision]
                with open(side, "x", encoding="utf-8") as fh:  # "x": never overwrite
                    fh.write(_xmp(rating, label))
                done.append({"from": str(src), "to": str(side)})
                return
            dest_dir = out / BUCKETS[decision]
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            if dest.exists():
                skipped.append({"name": src.name, "why": f"{dest} already exists"})
                return
            if mode == "move":
                shutil.move(str(src), str(dest))
            else:
                shutil.copy2(str(src), str(dest))
            done.append({"from": str(src), "to": str(dest)})
        except OSError as exc:
            skipped.append({"name": src.name, "why": f"could not {mode}: {exc.strerror or exc}"})

    def undo(self, args: dict, **_: Any) -> str:
        try:
            plan = self._get_plan(args.get("plan_id"))
            if plan is None or not plan.get("applied"):
                return _err("nothing to undo for that review")
            applied = plan["applied"]
            if applied["mode"] != "move":
                return _err(f"undo only reverses 'move'. This review used '{applied['mode']}', "
                            "which never touched the originals; the extra files can be deleted by hand.")
            restored, problems = 0, []
            for item in applied["done"]:
                src, dest = Path(item["to"]), Path(item["from"])
                if not src.exists():
                    problems.append(f"{src.name}: no longer in {src.parent}")
                elif dest.exists():
                    problems.append(f"{dest.name}: a file with that name is back in the original folder")
                else:
                    shutil.move(str(src), str(dest))
                    restored += 1
            for bucket in BUCKETS.values():
                d = Path(plan["folder"]) / OUT_DIR / bucket
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
            out = Path(plan["folder"]) / OUT_DIR
            if out.is_dir() and not any(out.iterdir()):
                out.rmdir()
            plan["applied"] = None if not problems else {**applied, "done": [
                i for i in applied["done"] if Path(i["to"]).exists()]}
            self._save_plan(plan)
            return _ok({"restored": restored, "problems": problems[:LIST_LIMIT]})
        except Exception as exc:
            return _err(f"undo failed: {exc}")

    def feedback(self, args: dict, **_: Any) -> str:
        try:
            verdict = (args.get("verdict") or "").lower()
            if verdict not in ("keep", "reject"):
                return _err("verdict must be keep or reject")
            plan = self._get_plan(args.get("plan_id"))
            if plan is None:
                return _err("no review found; run pickwise_review first")
            wanted = str(args.get("photo") or "").strip()
            if not wanted:
                return _err("which photo? give its file name")
            def names(d):
                return [d["name"].lower()] + [Path(c).name.lower() for c in d.get("companions", [])]

            matches = [d for d in plan["decisions"]
                       if wanted.lower() in names(d) or d["path"] == wanted]
            if not matches:
                matches = [d for d in plan["decisions"]
                           if any(wanted.lower() in n for n in names(d))]
            if len(matches) != 1:
                return _err("photo not found in the last review" if not matches
                            else "more than one photo matches; use the full file name",
                            candidates=[d["name"] for d in matches[:10]])
            d = matches[0]
            metrics = self._cached_metrics(d["path"])
            prefs = self._state_get("prefs", dict(engine.DEFAULT_PREFS))
            new_prefs, change = engine.learn(prefs, d, verdict, metrics)
            overrides = self._state_get("overrides", {})
            overrides[d["path"]] = verdict
            self._state_set("overrides", overrides)
            self._state_set("prefs", new_prefs)
            history = self._state_get("feedback", [])
            history.append({"at": time.time(), "photo": d["name"], "verdict": verdict,
                            "was": d["decision"], "reasons": d["reasons"], "change": change,
                            "note": str(args.get("note") or "")[:300]})
            self._state_set("feedback", history[-200:])
            return _ok({"photo": d["name"], "was": d["decision"], "now": verdict,
                        "learned": change, "preferences": new_prefs,
                        "folder": plan["folder"], "brief": plan.get("brief", {}),
                        "tip": "Call pickwise_review with no folder to re-run the latest review "
                               "(same folder and brief) and show the effect."})
        except Exception as exc:
            return _err(f"feedback failed: {exc}")

    def preferences(self, args: dict, **_: Any) -> str:
        try:
            action = (args.get("action") or "show").lower()
            if action == "reset":
                self._state_set("prefs", dict(engine.DEFAULT_PREFS))
                self._state_set("overrides", {})
                self._state_set("feedback", [])
                return _ok({"reset": True, "preferences": dict(engine.DEFAULT_PREFS)})
            prefs = self._state_get("prefs", dict(engine.DEFAULT_PREFS))
            history = self._state_get("feedback", [])
            return _ok({"preferences": prefs, "explained": _explain_prefs(prefs),
                        "photo_overrides": len(self._state_get("overrides", {})),
                        "recent_feedback": history[-10:]})
        except Exception as exc:
            return _err(f"preferences failed: {exc}")

    def preview(self, args: dict, **_: Any) -> str:
        """Make JPEG previews (from JPEG, HEIC or RAW) for a visual second look.

        Only writes inside Pickwise's own data folder. Nothing is sent anywhere:
        the agent decides, with the user's consent, whether to look at them.
        """
        try:
            plan = self._get_plan(args.get("plan_id"))
            if plan is None:
                return _err("no review found; run pickwise_review first")
            which = (args.get("which") or "picks").lower()
            bucket = {"picks": "pick", "maybe": "maybe", "rejects": "reject"}.get(which)
            wanted = [str(n).strip().lower() for n in (args.get("photos") or []) if str(n).strip()]
            decisions = plan["decisions"]
            if wanted:
                rows = [d for d in decisions
                        if d["name"].lower() in wanted or Path(d["name"]).stem.lower() in wanted]
                missing = [w for w in wanted if not any(
                    w in (d["name"].lower(), Path(d["name"]).stem.lower()) for d in rows)]
            else:
                if bucket is None:
                    return _err("which must be picks, maybe or rejects")
                rows = sorted((d for d in decisions if d["decision"] == bucket), key=lambda d: -d["score"])
                missing = []
            limit = _int(args.get("limit"), 6, 1, PREVIEW_LIMIT)
            total = len(rows)
            rows = rows[:limit]
            if not rows:
                return _err("no photos to preview", missing=missing)
            from plugins.plugin_storage import plugin_data_dir

            base = plugin_data_dir(PLUGIN) / "previews"
            out = base / plan["id"]
            out.mkdir(parents=True, exist_ok=True)

            def make(d: dict) -> dict:
                src = Path(d["path"])
                dest = out / (src.stem + ".jpg")
                try:
                    if not dest.exists():
                        im = analysis.open_photo(src, analysis.kind_of(src) or "other").convert("RGB")
                        im.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE))
                        im.save(dest, "JPEG", quality=85)
                    return {"name": d["name"], "preview": str(dest), "decision": d["decision"],
                            "reasons": d["reasons"]}
                except Exception as exc:
                    return {"name": d["name"], "error": f"could not make a preview: {exc}"}

            with ThreadPoolExecutor(max_workers=4) as pool:
                previews = list(pool.map(make, rows))
            sets = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
            for old in sets[:-KEEP_PREVIEW_SETS]:
                shutil.rmtree(old, ignore_errors=True)
            return _ok({
                "plan_id": plan["id"], "previews": previews, "shown": len(rows), "available": total,
                "not_found": missing,
                "how_to_check": (
                    "Pickwise cannot see eyes, expressions, composition or whether the subject itself "
                    "is sharp. Look at the previews with your vision tool ONE AT A TIME, not in "
                    "parallel (vision providers rate-limit bursts). Check what the user asked for; by "
                    "default: is there a person or clear subject, are eyes closed or half closed, is the "
                    "subject blurred by motion or missed focus, and is there a clear composition "
                    "mistake (tilted horizon, head or limbs cut off, something blocking the subject)? "
                    "Report mistakes, not taste. Then report per photo and offer pickwise_feedback "
                    "(verdict reject) for the ones the user agrees to drop."),
                "privacy": ("These files are local previews. Looking at them with a vision tool sends "
                            "them to the model provider: only do it because the user's current message "
                            "asked for a visual check. Do not repeat the check later unasked."),
            })
        except Exception as exc:
            return _err(f"preview failed: {exc}")

    # ── slash command ───────────────────────────────────────────────────────

    def slash(self, raw_args: str, **_: Any) -> str:
        arg = (raw_args or "").strip()
        if arg in ("", "help"):
            return ("Usage: /pickwise <folder>   review a folder (nothing is moved)\n"
                    "       /pickwise prefs      show what Pickwise has learned\n"
                    "       /pickwise reset      forget learned preferences\n"
                    "Or just ask Hermes, e.g. 'pick the 30 best from ~/Pictures/trip, dark is fine'.")
        if arg == "prefs":
            data = json.loads(self.preferences({"action": "show"}))
            return "Pickwise preferences: " + "; ".join(data.get("explained", [])) if "explained" in data else data.get("error", "")
        if arg == "reset":
            self.preferences({"action": "reset"})
            return "Pickwise preferences reset to defaults."
        data = json.loads(self.review({"folder": arg}))
        if "error" in data:
            return f"Pickwise: {data['error']}"
        t = data["totals"]
        lines = [f"Pickwise reviewed {data['photos']} photos in {data['folder']} ({data['seconds']}s):",
                 f"  {t['pick']} picks, {t['maybe']} maybe, {t['reject']} rejects, {data['burst_groups']} similar-photo groups."]
        for r in data["rejects"][:8]:
            lines.append(f"  x {r['name']}: {r['reasons'][0]}")
        lines.append(f"Full report: {data['report']}")
        lines.append("Nothing was moved. Ask Hermes to apply it (copy, move or xmp) when ready.")
        return "\n".join(lines)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _cached_metrics(self, path: str) -> dict:
        try:
            row = self._conn().execute("SELECT data FROM metrics WHERE path = ?", (path,)).fetchone()
            return json.loads(row[0]) if row else {}
        except Exception:
            return {}

    def _plans_table(self):
        conn = self._conn()
        conn.execute("CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, created REAL, data TEXT)")
        return conn

    def _save_plan(self, plan: dict) -> None:
        """Reviews live in Pickwise's own SQLite file, not in ctx.state: a review of
        thousands of photos would otherwise eat into Hermes' 10 MiB state budget."""
        conn = self._plans_table()
        conn.execute("INSERT OR REPLACE INTO plans(id, created, data) VALUES (?,?,?)",
                     (plan["id"], plan["created"], json.dumps(plan)))
        conn.execute("DELETE FROM plans WHERE id NOT IN "
                     "(SELECT id FROM plans ORDER BY created DESC LIMIT ?)", (KEEP_PLANS,))
        conn.commit()
        self._state_set("last_plan", plan["id"])
        if self._state_get("plans", None):  # migrate away from the 0.1.x state layout
            self._state_set("plans", {})

    def _get_plan(self, plan_id: Optional[str]) -> Optional[dict]:
        pid = plan_id or self._state_get("last_plan", None)
        if not pid:
            return None
        row = self._plans_table().execute("SELECT data FROM plans WHERE id = ?", (pid,)).fetchone()
        if row:
            return json.loads(row[0])
        legacy = self._state_get("plans", {})  # reviews saved by older versions
        return legacy.get(pid) if isinstance(legacy, dict) else None

    def _write_report(self, plan: dict, metrics: list[dict]) -> str:
        by_path = {m["path"]: m for m in metrics}
        lines = [f"# Pickwise report: {plan['folder']}", "",
                 f"Review {plan['id']}, brief: {json.dumps(plan['brief'])}", ""]
        for label, key in (("Picks", "pick"), ("Maybe", "maybe"), ("Rejects", "reject")):
            rows = [d for d in plan["decisions"] if d["decision"] == key]
            lines += [f"## {label} ({len(rows)})", "",
                      "| Photo | Score | Why | Sharpness | Brightness | Analysed size |",
                      "|---|---|---|---|---|---|"]
            for d in rows:
                m = by_path.get(d["path"], {})
                name = d["name"] + "".join(f" + {Path(c).name}" for c in d.get("companions", []))
                lines.append(f"| {name} | {d['score']} | {'; '.join(d['reasons'])} | "
                             f"{m.get('sharpness', '')} | {m.get('brightness', '')} | "
                             f"{m.get('width', '')}x{m.get('height', '')} |")
            lines.append("")
        reports = self._reports_dir()
        path = reports / f"{plan['id']}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        old_reports = sorted(reports.glob("*.md"), key=lambda p: p.stat().st_mtime)
        for stale in old_reports[:-KEEP_REPORTS]:
            stale.unlink(missing_ok=True)
        return str(path)

    def _summary(self, plan, decisions, metrics, prefs, overrides, skipped, seconds, report) -> dict:
        totals = {k: sum(1 for d in decisions if d["decision"] == k) for k in BUCKETS}
        kinds: dict[str, int] = {}
        for m in metrics:
            kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
        groups = {}
        for d in decisions:
            groups.setdefault(d["group"], []).append(d)
        bursts = [g for g in groups.values() if len(g) > 1]

        paired = {d["path"]: [Path(c).name for c in d["companions"]]
                  for d in plan["decisions"] if d.get("companions")}

        def short(rows, n=LIST_LIMIT):
            return [{"name": r["name"], "score": r["score"], "reasons": r["reasons"],
                     **({"paired_with": paired[r["path"]]} if r["path"] in paired else {})}
                    for r in rows[:n]]

        picks = sorted((d for d in decisions if d["decision"] == "pick"), key=lambda r: -r["score"])
        return {
            "plan_id": plan["id"], "folder": plan["folder"], "photos": len(metrics),
            "file_types": kinds, "seconds": seconds, "totals": totals,
            "burst_groups": len(bursts), "brief_applied": plan["brief"],
            **({"raw_jpeg_pairs": len(paired),
                "pairs_note": "RAW+JPEG pairs count as one photo: judged on the JPEG, and both "
                              "files always go to the same pile."} if paired else {}),
            "learned_preferences": _explain_prefs(prefs),
            "photo_overrides_applied": sum(1 for p in overrides if p in plan["files"]),
            "picks": short(picks), "maybe": short([d for d in decisions if d["decision"] == "maybe"]),
            "rejects": short([d for d in decisions if d["decision"] == "reject"]),
            "lists_truncated_at": LIST_LIMIT, "not_reviewed_over_limit": skipped,
            "report": report,
            "nothing_written": "No photos were moved or changed. Use pickwise_apply (dry run first) "
                               "to sort them into folders or write ratings.",
        }


def _explain_prefs(prefs: dict) -> list[str]:
    out = []
    scale = float(prefs.get("blur_scale", 1.0))
    if scale < 0.95:
        out.append("more tolerant of softness than default")
    elif scale > 1.05:
        out.append("stricter about sharpness than default")
    if prefs.get("dark_ok", 0) >= 2:
        out.append("keeps dark and silhouette shots")
    if prefs.get("bright_ok", 0) >= 2:
        out.append("keeps bright, high-key shots")
    return out or ["default settings (no corrections yet)"]


def _xmp(rating: str, label: str) -> str:
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Pickwise Photos">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
        f'   xmp:Rating="{rating}" xmp:Label="{label}"/>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n'
        '<?xpacket end="w"?>\n'
    )
