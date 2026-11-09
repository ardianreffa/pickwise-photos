# Pickwise Photos

**Sort the blurry, keep the best, and know why.**

Pickwise Photos is a Hermes Agent plugin that culls a folder of photos for you. Tell Hermes
what the shoot was and what you want, in plain words, and Pickwise reviews every photo on
your computer, picks the keepers, and explains each decision. It learns from your corrections,
so the next folder follows your taste.

It works for anyone with too many photos: a photographer after a job, or a phone full of
holiday bursts.

```text
You:     Here are yesterday's concert photos in ~/Pictures/concert. Dark is fine, it was a
         night show. Pick the 30 best for Instagram, no near-duplicates.
Hermes:  Reviewed 212 photos: 30 picks, 41 maybe, 141 rejects.
         I grouped 38 bursts and kept the sharpest frame of each. Rejected IMG_0412 and
         IMG_0413 as blurry (nothing in focus) ... Nothing has been moved yet.
You:     Keep IMG_0544, the motion blur is on purpose.
Hermes:  Noted. Pickwise will be more tolerant of soft shots like that one. Want me to
         review again, then copy the picks into a Picks folder?
```

## What makes it different

1. **It follows a brief, not sliders.** "Concert, dark is fine", "the panning blur is on
   purpose", "best 20 for the client": Hermes turns your words into the review settings.
2. **Every decision has a reason.** "Blurry: no part of the photo is in focus", "too dark",
   "near-duplicate of IMG_0231, which scored higher". You can disagree, and say why.
3. **It learns your taste.** Corrections are remembered. Keep two dark shots it doubted and
   it keeps dark shots from then on. Mark a soft photo as a keeper and it relaxes for similar
   ones.
4. **Local and safe.** Photos never leave your computer and are never deleted or edited.

## What it checks

| Check | How |
|---|---|
| Focus and camera shake | A "re-blur" test on the most detailed parts of the photo: a sharp photo loses most of its fine detail when blurred a little more, a blurry one barely changes. Measuring the most detailed areas means a sharp subject on a soft background still counts as sharp. |
| Exposure | Too dark, blown-out highlights, and low-key or silhouette shots, which are flagged as "maybe" instead of rejected. |
| Bursts and near-duplicates | Capture time (to the fraction of a second) plus a perceptual hash, compared frame to frame: a photo joins a burst only if it was taken within 2 seconds of the previous one and looks like it. The pause while a subject changes pose starts a new group, so every pose keeps its best frame. |
| Scene | `people` (default): one sharp area is enough, so a blurred background is fine. `scenery`: most of the frame must be sharp, so a landscape that is only sharp in one corner is flagged. |
| Ranking | A 0-100 score from sharpness and exposure, used for "best N" and inside bursts. |

Supported files: JPEG, HEIC/HEIF (iPhone), PNG, TIFF, WebP, and camera RAW (CR2, CR3, NEF,
ARW, DNG, RAF, ORF, RW2 and more, including iPhone DNG / ProRAW), read through the
full-size colour preview that cameras and phones embed in RAW files, so no RAW decoder is
needed. Grayscale depth maps and masks inside iPhone DNGs are skipped.

Works best on original files from the camera or phone. Photos that went through a chat app
or social network are still judged, but those apps strip capture times, so burst grouping
falls back to image similarity only.

## Install

```bash
hermes plugins install ardianreffa/pickwise-photos --enable
```

Pickwise uses Pillow and pillow-heif, which already ship with Hermes, so the install adds
nothing heavy. Requires Hermes 0.23.0 or newer. If Hermes itself runs the install for you
(non-interactive), add `--yes-deps` so the dependency question is answered up front.

After `hermes plugins update pickwise-photos`, run `hermes gateway restart` (or restart
Hermes Desktop) so the running gateway loads the new code.

## Use

Just ask Hermes, for example:

- "Pick the best photos from ~/Pictures/bali-trip"
- "Cull ~/Desktop/wedding-raw, keep 50, motion blur is fine"
- "Pick the sharpest landscapes from ~/Pictures/hike" (uses `scene: scenery`)
- "Why did you reject IMG_2041?" / "Keep IMG_2041, I like it"
- "Copy the picks into folders" / "Write ratings for Lightroom"

Or type `/pickwise ~/Pictures/bali-trip` for a quick review (`/pickwise prefs` shows what it
has learned, `/pickwise reset` forgets it).

### Tools

| Tool | What it does | Writes files? |
|---|---|---|
| `pickwise_review` | Analyses a folder, applies the brief and learned preferences, returns pick / maybe / reject with reasons and a full Markdown report. | No (report goes to Pickwise's own data folder) |
| `pickwise_apply` | Dry run by default. With `confirm: true`: `copy` into `Pickwise/Picks`, `Pickwise/Maybe`, `Pickwise/Rejects`; `move` there instead; or `xmp` rating sidecars. | Only after you confirm |
| `pickwise_feedback` | Records your correction for one photo and adjusts future reviews. | No |
| `pickwise_preferences` | Shows or resets what Pickwise has learned. | No |
| `pickwise_undo` | Moves photos back after a `move`. | Moves files back |

### Lightroom and darktable

`pickwise_apply` with `mode: xmp` writes a sidecar with a star rating and colour label
(picks 5 stars / green, maybe 3 / yellow, rejects "rejected" / red). Lightroom Classic reads
sidecars for RAW files (`IMG_0001.xmp`); darktable reads them for every format
(`IMG_0001.jpg.xmp`). Lightroom stores JPEG and HEIC ratings inside the file itself, which
Pickwise never edits, so for those use `copy` mode and import the `Picks` folder. Existing
sidecars are never replaced.

## Privacy, safety and disclosure

- **Reads:** only the folder you name (and its subfolders if you ask), and only image files.
- **Writes:** nothing until you confirm `pickwise_apply`. Then, only inside a `Pickwise/`
  folder in the reviewed folder (copy/move), or `.xmp` files next to photos that have none.
  `move` is reversible with `pickwise_undo`. No photo is ever deleted, overwritten or edited.
- **Stores** in Hermes' plugin data folder (`~/.hermes/plugin-data/pickwise-photos/`): a cache
  of per-photo measurements (sharpness, brightness, a 64-bit image hash, capture time, file
  path), review reports, and your learned preferences and corrections. No image data.
- **No network access, no model calls, no shell commands, no telemetry.** All analysis runs
  locally with Pillow.
- The `move` mode and the `xmp` mode change your folder's contents, so Hermes is instructed to
  show a dry run and wait for your explicit yes. In unattended runs (cron, gateway) nothing is
  written without `confirm: true`, and nothing ever waits for input.

## Limitations

Pickwise judges focus, exposure and duplicates. It does not judge composition, emotion,
smiles or closed eyes, and it does not recognise people; Hermes will say so if your brief
asks for that. Because it does not know where the person is, it cannot tell whether focus
landed on the subject or on the background: a sharp tree behind a soft face still reads as
"sharp". For a handful of photos you can ask Hermes to look at them with its own vision
tool, which sends those images to your model provider, so it only happens when you ask. Very noisy night shots can look sharper than they are. RAW files are judged
from their embedded preview, which reflects the camera's own processing.

## Development

```bash
python -m pytest                                    # unit tests (generate their own photos)
hermes plugins validate . --install-deps            # catalog admission checks
hermes plugins doctor . --ci                        # real runtime registration check
```

## License

MIT
