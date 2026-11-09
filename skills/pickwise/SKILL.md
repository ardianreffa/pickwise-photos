---
name: pickwise
description: How to cull a folder of photos with Pickwise Photos, from brief to sorted folders.
---

# Culling photos with Pickwise

1. **Get the folder and the brief.** Ask only if the folder is missing. Turn the brief into
   parameters for `pickwise_review`: dark or night or concert shots are fine -> `keep_dark`;
   snow, white studio or beach -> `keep_bright`; panning or motion blur on purpose ->
   `allow_soft`; "best N" -> `count`; "keep every variation" -> `dedupe: false`;
   "keep the best 3 of each burst" -> `per_burst: 3`;
   landscapes, architecture or interiors -> `scene: scenery`; people, events, sport or
   pets -> `scene: people`.
   Pass the user's own words as `brief_note`.
2. **Review.** Call `pickwise_review`. Summarise the totals, then name a few rejects with
   their reasons, in the user's language. Mention near-duplicate groups.
3. **Be honest about limits.** Pickwise judges focus, exposure and duplicates. It does not
   judge smiles, composition or who is in the photo, and it cannot tell whether focus landed
   on the person or on the background; say so if the brief asks for that.
   If the user wants a closer look at a few specific photos (for example "is the focus on
   me in these maybes?"), you may offer to inspect those photos with your own vision tool,
   but only after the user agrees, and say that this sends those images to the model
   provider. Never do it for a whole folder by default.
4. **Corrections.** If the user disagrees with a decision, call `pickwise_feedback` for that
   photo, tell them what Pickwise learned, and offer to review again.
5. **Apply only with consent.** Call `pickwise_apply` without `confirm` first (dry run), show
   what will happen, and call it with `confirm: true` only after the user says yes. Prefer
   `copy` unless they ask to move. Offer `xmp` to Lightroom Classic or darktable users.
6. After a `move`, remind them `pickwise_undo` can put everything back.
