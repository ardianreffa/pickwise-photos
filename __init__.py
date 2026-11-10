"""Pickwise Photos: pick your best photos, with reasons. Local, no network."""

from __future__ import annotations

from pathlib import Path

TOOLSET = "pickwise"


def register(ctx):
    from . import schemas
    from .tools import Pickwise

    pw = Pickwise(ctx)
    ctx.register_tool(name="pickwise_review", toolset=TOOLSET, schema=schemas.REVIEW,
                      handler=pw.review, emoji="📸")
    ctx.register_tool(name="pickwise_apply", toolset=TOOLSET, schema=schemas.APPLY,
                      handler=pw.apply, emoji="📂")
    ctx.register_tool(name="pickwise_feedback", toolset=TOOLSET, schema=schemas.FEEDBACK,
                      handler=pw.feedback, emoji="👍")
    ctx.register_tool(name="pickwise_preferences", toolset=TOOLSET, schema=schemas.PREFERENCES,
                      handler=pw.preferences, emoji="⚙️")
    ctx.register_tool(name="pickwise_undo", toolset=TOOLSET, schema=schemas.UNDO,
                      handler=pw.undo, emoji="↩️")
    ctx.register_tool(name="pickwise_preview", toolset=TOOLSET, schema=schemas.PREVIEW,
                      handler=pw.preview, emoji="🔍")
    ctx.register_command("pickwise", handler=pw.slash,
                         description="Review a photo folder: /pickwise <folder> | prefs | reset")
    skill = Path(__file__).parent / "skills" / "pickwise" / "SKILL.md"
    if skill.exists():
        ctx.register_skill("pickwise", skill)
