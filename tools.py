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
MAX_PHOTOS_DEFAULT = 2000
LIST_LIMIT = 40
KEEP_PLANS = 10


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

    def _metrics_for(self, paths: list[Path]) -> list[dict]:
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
            if row and row[0] == st.st_size and abs(row[1] - st.st_mtime) < 1e-6:
                cached[str(p)] = json.loads(row[2])
            else:
                todo.append(p)
        if todo:
            workers = max(2, min(8, (os.cpu_count() or 4)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                fresh = list(pool.map(analysis.analyze, todo))
            for m in fresh:
                d = m.to_dict()
                cached[m.path] = d
                if not m.error:
                    conn.execute("INSERT OR REPLACE INTO metrics(path, size, mtime, data) VALUES (?,?,?,?)",
                                 (m.path, m.size, m.mtime, json.dumps(d)))
            conn.commit()
        return [cached[str(p)] for p in paths if str(p) in cached]

    # ── tools ───────────────────────────────────────────────────────────────

    def review(self, args: dict, **_: Any) -> str:
        try:
            folder = resolve_folder(args.get("folder", ""))
        except ValueError as exc:
            return _err(str(exc))
        try:
            limit = int(args.get("max_photos") or MAX_PHOTOS_DEFAULT)
            paths, skipped = analysis.list_photos(folder, bool(args.get("recursive")), limit)
            if not paths:
                return _err("no supported photos in this folder",
                            folder=str(folder), supported=sorted(analysis.SUPPORTED_EXT))
            started = time.time()
            metrics = self._metrics_for(paths)
            brief = {
                "count": args.get("count") or None,
                "keep_dark": bool(args.get("keep_dark")),
                "keep_bright": bool(args.get("keep_bright")),
                "allow_soft": bool(args.get("allow_soft")),
                "dedupe": args.get("dedupe", True) is not False,
                "strictness": args.get("strictness") or "normal",
            }
            prefs = self._state_get("prefs", dict(engine.DEFAULT_PREFS))
            overrides = self._state_get("overrides", {})
            decisions = engine.decide(metrics, brief, prefs, overrides)
            plan_id = uuid.uuid4().hex[:8]
            plan = {
                "id": plan_id, "folder": str(folder), "created": time.time(), "brief": brief,
                "brief_note": str(args.get("brief_note") or "")[:500],
                "decisions": [{k: d[k] for k in ("name", "path", "decision", "reasons", "score", "group")}
                              for d in decisions],
                "files": {m["path"]: [m["size"], m["mtime"]] for m in metrics},
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
            if not args.get("confirm"):
                where = (f"subfolders Picks/Maybe/Rejects inside {out}" if mode != "xmp"
                         else "a .xmp rating sidecar next to each photo (existing sidecars are left alone)")
                return _ok({
                    "dry_run": True, "plan_id": plan["id"], "mode": mode, "counts": counts,
                    "would_write": where,
                    "originals": ("moved (undo with pickwise_undo)" if mode == "move"
                                  else "left untouched"),
                    "next_step": "Show this to the user. Only call again with confirm=true after "
                                 "they explicitly agree.",
                })
            if plan.get("applied"):
                return _err(f"this review was already applied ({plan['applied']['mode']}); "
                            "run a new review to apply again")
            done, skipped = [], []
            for d in plan["decisions"]:
                src = Path(d["path"])
                size_mtime = plan["files"].get(d["path"])
                try:
                    st = src.stat()
                except OSError:
                    skipped.append({"name": d["name"], "why": "file no longer exists"})
                    continue
                if size_mtime and (st.st_size != size_mtime[0] or abs(st.st_mtime - size_mtime[1]) > 1e-6):
                    skipped.append({"name": d["name"], "why": "file changed since the review"})
                    continue
                if mode == "xmp":
                    side = src.with_suffix(".xmp") if analysis.kind_of(src) == "raw" else src.with_name(src.name + ".xmp")
                    if side.exists():
                        skipped.append({"name": d["name"], "why": f"{side.name} already exists"})
                        continue
                    rating, label = XMP_RATING[d["decision"]]
                    side.write_text(_xmp(rating, label), encoding="utf-8")
                    done.append({"from": str(src), "to": str(side)})
                    continue
                dest_dir = out / BUCKETS[d["decision"]]
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / src.name
                if dest.exists():
                    skipped.append({"name": d["name"], "why": f"{dest} already exists"})
                    continue
                if mode == "move":
                    shutil.move(str(src), str(dest))
                else:
                    shutil.copy2(str(src), str(dest))
                done.append({"from": str(src), "to": str(dest)})
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
            matches = [d for d in plan["decisions"]
                       if d["name"].lower() == wanted.lower() or d["path"] == wanted]
            if not matches:
                matches = [d for d in plan["decisions"] if wanted.lower() in d["name"].lower()]
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
                        "tip": "Run pickwise_review again to see the effect on the whole folder."})
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

    def _save_plan(self, plan: dict) -> None:
        plans = self._state_get("plans", {})
        plans[plan["id"]] = plan
        if len(plans) > KEEP_PLANS:
            for old in sorted(plans, key=lambda k: plans[k]["created"])[:-KEEP_PLANS]:
                plans.pop(old)
        self._state_set("plans", plans)
        self._state_set("last_plan", plan["id"])

    def _get_plan(self, plan_id: Optional[str]) -> Optional[dict]:
        plans = self._state_get("plans", {})
        pid = plan_id or self._state_get("last_plan", None)
        return plans.get(pid) if pid else None

    def _write_report(self, plan: dict, metrics: list[dict]) -> str:
        by_path = {m["path"]: m for m in metrics}
        lines = [f"# Pickwise report: {plan['folder']}", "",
                 f"Review {plan['id']}, brief: {json.dumps(plan['brief'])}", ""]
        for label, key in (("Picks", "pick"), ("Maybe", "maybe"), ("Rejects", "reject")):
            rows = [d for d in plan["decisions"] if d["decision"] == key]
            lines += [f"## {label} ({len(rows)})", "", "| Photo | Score | Why | Sharpness | Brightness |",
                      "|---|---|---|---|---|"]
            for d in rows:
                m = by_path.get(d["path"], {})
                lines.append(f"| {d['name']} | {d['score']} | {'; '.join(d['reasons'])} | "
                             f"{m.get('sharpness', '')} | {m.get('brightness', '')} |")
            lines.append("")
        path = self._reports_dir() / f"{plan['id']}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
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

        def short(rows, n=LIST_LIMIT):
            return [{"name": r["name"], "score": r["score"], "reasons": r["reasons"]} for r in rows[:n]]

        picks = sorted((d for d in decisions if d["decision"] == "pick"), key=lambda r: -r["score"])
        return {
            "plan_id": plan["id"], "folder": plan["folder"], "photos": len(metrics),
            "file_types": kinds, "seconds": seconds, "totals": totals,
            "burst_groups": len(bursts), "brief_applied": plan["brief"],
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
