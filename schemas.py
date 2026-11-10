"""Pickwise Photos: tool schemas (what the model reads)."""

REVIEW = {
    "name": "pickwise_review",
    "description": (
        "Review a folder of photos and decide which are the best ones to keep, locally on this "
        "computer (JPEG, HEIC/HEIF, PNG/TIFF/WebP, and camera RAW via its embedded preview). "
        "Finds blurry, too-dark and blown-out shots, groups bursts and near-duplicates and keeps "
        "the best of each, and returns pick / maybe / reject with a plain-language reason for "
        "every photo. Read-only: it never moves or changes files. Use it whenever the user wants "
        "to cull, sort, choose, pick or clean up photos. Translate the user's brief into the "
        "parameters, e.g. 'concert, dark is fine' -> keep_dark=true; 'motion blur is on purpose' "
        "-> allow_soft=true; 'best 30 for Instagram' -> count=30; 'landscapes from my hike' -> "
        "scene=scenery; 'keep the best 3 of each burst' -> per_burst=3. Pickwise measures focus anywhere in the frame; it cannot tell whether focus "
        "landed on the person or on the background. If the brief asks for something these "
        "parameters cannot judge (smiles, composition, who is in the photo, focus on the subject), "
        "say so plainly instead of pretending. Reasons are in English; relay them in the user's language."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "folder": {"type": "string",
                       "description": "Folder with the photos. Absolute, ~/..., or relative to the session folder. Omit it to re-run the latest review with the same folder and brief (e.g. after pickwise_feedback); any other parameter you pass overrides that brief."},
            "count": {"type": "integer", "minimum": 1,
                      "description": "Keep only the N best as picks; other good photos become maybe."},
            "keep_dark": {"type": "boolean",
                          "description": "Dark or low-key photos are intentional (night, concert, silhouettes): do not reject them for darkness."},
            "keep_bright": {"type": "boolean",
                            "description": "Bright or high-key photos are intentional (snow, white studio, beach): do not reject them for brightness."},
            "allow_soft": {"type": "boolean",
                           "description": "Softness or motion blur is intentional (panning, slow shutter, dreamy look): never reject for blur, mark as maybe at most."},
            "dedupe": {"type": "boolean",
                       "description": "Keep only the best photo(s) of each burst / near-duplicate group (default true). false keeps every frame."},
            "per_burst": {"type": "integer", "minimum": 1, "maximum": 20,
                          "description": "How many of the best frames to keep from each burst (default 1). Use it when the photographer bursts to choose between expressions or moments, e.g. 'keep the best 3 of each burst'."},
            "scene": {"type": "string", "enum": ["auto", "people", "scenery"],
                      "description": "What the photos are of. 'people' (portraits, events, sport, pets): one sharp area is enough, a blurred background is fine. 'scenery' (landscapes, architecture, interiors): most of the frame should be sharp. Default auto behaves like people. Ask the user if it is unclear and it matters."},
            "strictness": {"type": "string", "enum": ["relaxed", "normal", "strict"],
                           "description": "How strict the sharpness check is (default normal)."},
            "recursive": {"type": "boolean", "description": "Also look in subfolders (default false)."},
            "max_photos": {"type": "integer", "minimum": 1, "maximum": 10000,
                           "description": "Safety cap on photos reviewed (default 2000)."},
            "brief_note": {"type": "string",
                           "description": "The user's brief in their own words, stored with the review for reference."},
        },
    },
}

APPLY = {
    "name": "pickwise_apply",
    "description": (
        "Act on a Pickwise review. Without confirm=true this is a dry run that only describes what "
        "would happen: always do the dry run first, show it to the user, and call again with "
        "confirm=true only after the user explicitly agrees. Modes: 'copy' (default) copies photos "
        "into Pickwise/Picks, Pickwise/Maybe and Pickwise/Rejects inside the reviewed folder and leaves "
        "the originals untouched; 'move' moves them there instead (reversible with pickwise_undo); "
        "'xmp' writes a star-rating sidecar file next to each photo for Lightroom Classic (RAW) or "
        "darktable, never replacing an existing sidecar. Photos are never deleted or edited."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "Review id from pickwise_review (default: the latest review)."},
            "mode": {"type": "string", "enum": ["copy", "move", "xmp"], "description": "How to apply (default copy)."},
            "confirm": {"type": "boolean",
                        "description": "true only after the user explicitly approved the dry run. Omit for a dry run."},
        },
    },
}

FEEDBACK = {
    "name": "pickwise_feedback",
    "description": (
        "Record the user's correction of one Pickwise decision so Pickwise learns their taste. Use it "
        "when the user says a rejected photo should be kept ('I like that blurry one, it was on purpose') "
        "or a pick should go. The photo is remembered, and repeated corrections shift future reviews "
        "(more tolerant of softness, keeps dark shots, and so on). Then run pickwise_review again to "
        "show the effect."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "photo": {"type": "string", "description": "File name (or full path) of the photo from the last review."},
            "verdict": {"type": "string", "enum": ["keep", "reject"], "description": "What the user wants for this photo."},
            "plan_id": {"type": "string", "description": "Review id (default: the latest review)."},
            "note": {"type": "string", "description": "The user's reason, in their words (optional)."},
        },
        "required": ["photo", "verdict"],
    },
}

PREFERENCES = {
    "name": "pickwise_preferences",
    "description": "Show what Pickwise has learned from the user's corrections, or reset it to defaults.",
    "parameters": {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["show", "reset"], "description": "Default show."}},
    },
}

UNDO = {
    "name": "pickwise_undo",
    "description": "Move photos back to where they were after a pickwise_apply in 'move' mode.",
    "parameters": {
        "type": "object",
        "properties": {"plan_id": {"type": "string", "description": "Review id (default: the latest review)."}},
    },
}

PREVIEW = {
    "name": "pickwise_preview",
    "description": (
        "Make local JPEG previews (from JPEG, HEIC/HEIF or RAW/DNG) of photos from the latest Pickwise "
        "review, so you can take a visual second look with your vision tool: closed or half-closed "
        "eyes, a subject blurred by motion while the background is sharp, missed focus on the person. "
        "Pickwise itself cannot judge these. Use it when the user asks for such a check, e.g. 'check my "
        "picks for closed eyes'. Viewing the previews with a vision tool sends them to the model "
        "provider, so do that only at the user's request. Look at them one at a time, not in parallel, "
        "because vision providers rate-limit bursts. Then report per photo and offer "
        "pickwise_feedback (verdict reject) for photos the user wants to drop. The previews are written "
        "only to Pickwise's own data folder."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "which": {"type": "string", "enum": ["picks", "maybe", "rejects"],
                      "description": "Which pile to preview, best first (default picks)."},
            "photos": {"type": "array", "items": {"type": "string"},
                       "description": "Specific file names to preview instead of a pile (e.g. ['IMG_7424.DNG'])."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 40,
                      "description": "Maximum number of previews (default 6; more means more vision calls)."},
            "plan_id": {"type": "string", "description": "Review id (default: the latest review)."},
        },
    },
}
