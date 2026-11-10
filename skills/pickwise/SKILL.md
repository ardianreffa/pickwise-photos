---
name: pickwise
description: How to cull a folder of photos with Pickwise Photos, from brief to sorted folders, including the opt-in visual second look.
---

# Culling photos with Pickwise

Pickwise is a correction tool, not an art critic: it removes clear failures, keeps the best
frame of each burst and explains every decision. Taste stays with the user.

1. **Get the folder and the brief.** Ask only if the folder is missing. Turn the brief into
   `pickwise_review` parameters:
   - "dark / night / concert shots are fine" -> `keep_dark`; "snow, beach, white studio" ->
     `keep_bright`; "the motion blur is on purpose" -> `allow_soft`
   - "I want moody low-key shots" -> `look: low_key`; "bright and airy" -> `look: high_key`
     (a look the user wants is ranked higher, not just tolerated)
   - "best N" -> `count`; "keep the best 3 of each burst" -> `per_burst: 3`;
     "keep every variation" -> `dedupe: false`
   - landscapes, architecture, interiors -> `scene: scenery`; people, events, sport, pets ->
     `scene: people`
   - pass the user's own words as `brief_note`
2. **Review.** Call `pickwise_review`. Summarise the totals, name a few rejects with their
   reasons in the user's language, and mention near-duplicate groups. To re-run the latest
   review (for example after a correction), call `pickwise_review` with no folder.
3. **Be honest about limits.** Pickwise's own analysis judges focus, exposure and duplicates.
   It cannot see eyes, expressions or composition, and cannot tell whether focus landed on the
   person or on the background. Say so if the brief depends on that, and mention the visual
   second look below.
4. **Visual second look (opt-in).** Only when the user's current message asks for it, call
   `pickwise_preview` (default: the top 6 picks; or `photos: [...]`), then look at the
   previews with your vision tool one at a time, never in parallel (providers rate-limit
   bursts). Check what they asked for; by default: is there a subject, are eyes closed, is the
   subject blurred, is there a clear composition mistake (tilted horizon, head or limbs cut
   off, something blocking the subject)? Point out mistakes, not taste: rule of thirds, colour
   and style are the photographer's choice unless they ask your opinion. Say once that this
   sends those previews to the model provider. Never do it on your own initiative, after a
   re-review, or for a whole folder. Offering it is fine; an offer you made earlier is not
   consent.
5. **Corrections.** If the user disagrees with a decision, call `pickwise_feedback` for that
   photo, tell them what Pickwise learned, and offer to review again. A rejected burst frame is
   replaced by the next best frame of that burst.
6. **Apply only with consent.** Call `pickwise_apply` without `confirm` first (dry run), show
   what will happen, and call it with `confirm: true` only after the user says yes. Prefer
   `copy` unless they ask to move. Offer `xmp` to Lightroom Classic or darktable users.
7. After a `move`, remind them `pickwise_undo` can put everything back.
