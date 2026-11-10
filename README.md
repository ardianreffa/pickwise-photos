# Pickwise Photos

**A correction tool, not an art critic.** Pickwise sorts hundreds of photos in seconds, drops
the clear failures, keeps the best frame of every burst and tells you why. On request, Hermes
takes a second look at the top picks for closed eyes, a blurry subject or a crooked horizon.
Taste stays yours.

For anyone with too many photos and no time to go through them one by one: a photographer
after a job, a runner after race day, or a phone full of holiday bursts. You don't need to
know any photography terms; you just say what you want.

```text
You:     Review ~/evening-run-shoot. Pick the 20 best and check the top ones for closed eyes
         or a blurry subject.
Hermes:  Reviewed 280 photos (139 HEIC, 140 DNG, 1 JPEG): 20 picks, 256 maybe, 4 rejects.
         Rejected IMG_7245, 7258 and 7259 (blurry, nothing in focus) and IMG_7441 (almost
         black). 40 bursts, best frame of each kept.
         I checked the top 4 picks. IMG_7365: the runner is blurred by motion and cut off at
         the right edge; Pickwise rated it sharp because the trees behind him are in focus.
         The other three look fine. Drop IMG_7365?
You:     Yes, drop it.
Hermes:  Done. IMG_7373 moves up into the top 20. Nothing has been moved yet; want me to copy
         the picks into folders?
```

*(Condensed from real tests on 280 iPhone photos; a full review took about 30 seconds on an M4 Pro.)*

**Thousands of photos?** Fine. Pickwise saves its work as it goes and analyses a big folder in
steps of about four minutes, so it never hits Hermes' time limit for a single tool. Hermes
tells you the progress and continues on its own; once analysed, a folder re-reviews in a
fraction of a second. Tested with 3,000 photos.

## Quick start

1. **Install**
   ```bash
   hermes plugins install ardianreffa/pickwise-photos --enable
   ```
2. **Ask Hermes in one sentence**, with the folder path and what you want:
   `Review ~/Pictures/bali-trip and pick the 30 best`
3. **Look at the result**, correct anything you disagree with, then say
   `copy them into folders`. You'll see a dry run first; nothing is written until you say yes.

Tip: to get a folder's path on a Mac, select it in Finder and press **Option + Command + C**,
then paste it into your message.

## How it works

1. **Fast local pass (Pickwise).** Every photo is measured on your computer: focus, exposure,
   bursts and near-duplicates. Each one gets pick / maybe / reject and a reason. No network,
   no AI model, no upload.
2. **Optional visual second look (Hermes).** Pickwise cannot see eyes, expressions or
   composition. When you ask, it makes small previews of the top picks and Hermes looks at
   them with its own vision tool. Only those previews leave your computer, and only then.
3. **You decide.** Pickwise rejects only what can't be fixed (blur, near-black or blown-out
   frames) and labels what an edit could rescue. Disagree with anything, and Pickwise remembers. Nothing moves until you
   confirm, originals are never edited, and moves can be undone.

## What you can ask for

| Situation | Say something like | What changes |
|---|---|---|
| Phone photos from a trip | "Pick the 30 best from ~/Pictures/bali-trip" | Keeps the best of each burst, drops blurry and badly exposed shots |
| Concert or night event | "Concert photos, dark is fine. Best 40" | Dark shots are no longer rejected |
| Moody or bright style | "I want moody low-key shots" / "bright and airy" | That look is ranked higher, not just tolerated |
| Sport, running, panning | "Motion blur is on purpose" | Soft shots become maybe at worst |
| Bursts where moments matter | "Keep the best 3 of each burst" | More frames per burst |
| Landscapes | "Pick the sharpest landscapes from ~/Pictures/hike" | Most of the frame must be sharp |
| People shots | "Check my top 6 picks for closed eyes or a blurry subject" | Visual second look by Hermes |
| Composition slips | "Any crooked horizons or cut-off heads in my top 10?" | Visual second look by Hermes |
| A correction | "Keep IMG_2041, I like it" / "Drop IMG_2042" | Remembered; similar photos shift a little next time |
| Again, after a correction | "Review again" | Same folder and brief, with your corrections |
| Lightroom or darktable | "Write ratings for Lightroom" | Star ratings in `.xmp` sidecars |
| RAW + JPEG from a camera | "Review ~/Pictures/DCIM-import, best 50" | Each pair counts once and stays together |

Or type `/pickwise ~/Pictures/bali-trip` for a quick review. `/pickwise prefs` shows what it
has learned and `/pickwise reset` forgets it.

## What Pickwise checks

| Check | How |
|---|---|
| Focus and camera shake | A "re-blur" test on the most detailed parts of the photo: a sharp photo loses most of its fine detail when blurred a little more, a blurry one barely changes. A sharp subject on a soft background still counts as sharp. |
| Exposure | Pickwise rejects what an edit can't fix (near-black frames, large blown-out areas) and keeps what it can: a photo that is a bit under- or overexposed becomes "maybe" with that reason ("a bit underexposed: fixable in editing, especially from RAW"), never a silent "well exposed". Low-key and silhouette shots become "maybe" too, and "dark is fine" or "low-key" in your brief turns them into picks. |
| Bursts and near-duplicates | Capture time (to the fraction of a second) plus a perceptual hash, frame to frame: a photo joins a burst only if it was taken within 2 seconds of the previous one and looks like it. The pause while a subject changes pose starts a new group, so every pose keeps its best frame. |
| Ranking | A 0–100 score from sharpness and exposure (or closeness to the look you asked for), used for "best N" and inside bursts. |

### Files and limits

| Kind | Formats | Notes |
|---|---|---|
| Phone | HEIC / HEIF, JPEG | Tested on iPhone photos |
| Camera RAW | DNG (incl. iPhone ProRAW), ARW, CR2, CR3, NEF, RAF, ORF, RW2, PEF, SRW, IIQ, 3FR and more | Judged from the colour preview embedded in the file. Tested on iPhone DNG; other RAW formats are read the same way |
| Other | PNG, TIFF, WebP | |
| Ignored | GIF, BMP, AVIF, JPEG XL, PSD, video, the video part of Live Photos | Skipped quietly |

Up to 400 MB per file and about 300 megapixels per photo; larger files are skipped with a
reason. Works best on original files: photos sent through chat apps lose their capture times,
so bursts are grouped by similarity only.

**RAW + JPEG shooters:** files with the same name (`DSC01234.ARW` + `DSC01234.JPG`) are one
photo. Pickwise judges the full-resolution JPEG and always moves, copies or rates both files
together, so a pair never ends up split between piles. This matters because some cameras
(several Sony bodies, for example) embed only a small preview of about 1.7 MP in their RAW
files: RAW-only shoots from those cameras are still judged, but subtle focus misses are easier
to catch with RAW + JPEG. The report's "Analysed size" column shows what was measured.

## Visual second look

Pickwise points out **mistakes, not taste**. When you ask, Hermes checks the photos you choose
(the top 6 picks by default) for:

- closed or half-closed eyes
- a subject blurred by motion or missed focus while the background is sharp
- a frame with no subject at all
- clear composition mistakes: a tilted horizon, a head or limbs cut off by the frame edge,
  something blocking the subject
- expressions such as smiling, if you ask

Rule of thirds, colour and style are your call; Hermes gives an opinion on those only if you
ask for one. Each preview is checked one at a time. Dropping a photo lets the next best one
(from the same burst, if it was part of one) take its place.

**Tip:** image analysis uses your main model by default. Large models can be slow or hit rate
limits on images; a fast vision model works well here:

```bash
hermes config set auxiliary.vision.provider nous
hermes config set auxiliary.vision.model google/gemini-3.7-flash
```

## Where the results go

- **Folders:** `copy` (default) puts copies in `Pickwise/Picks`, `Pickwise/Maybe` and
  `Pickwise/Rejects` inside the reviewed folder. `move` moves them instead, and
  `pickwise_undo` puts them back.
- **Lightroom Classic / darktable:** `xmp` writes a sidecar next to each photo (picks 5 stars
  / green, maybe 3 / yellow, rejects "rejected" / red). Lightroom Classic reads sidecars for RAW
  files (`IMG_0001.xmp`); darktable reads them for every format (`IMG_0001.jpg.xmp`).
  Lightroom keeps JPEG and HEIC ratings inside the file itself, which Pickwise never edits, so
  for those use `copy` and import the `Picks` folder. Existing sidecars are never replaced.

### Tools

| Tool | What it does | Writes files? |
|---|---|---|
| `pickwise_review` | Analyses a folder, applies the brief and what it has learned, returns pick / maybe / reject with reasons and a Markdown report. With no folder, re-runs the latest review. | No (report goes to Pickwise's data folder) |
| `pickwise_apply` | Dry run by default. With `confirm: true`: `copy`, `move` or `xmp`. | Only after you confirm |
| `pickwise_feedback` | Records your correction for one photo and adjusts future reviews gradually. | No |
| `pickwise_preferences` | Shows or resets what Pickwise has learned. | No |
| `pickwise_undo` | Moves photos back after a `move`. | Moves files back |
| `pickwise_preview` | Makes local JPEG previews (also from HEIC and RAW) for the visual second look. | Previews in Pickwise's data folder only |

## Performance and resources

- **Speed:** roughly 0.1–0.3 s per photo on a recent Mac (iPhone DNG and JPEG are the fastest,
  48 MP HEIC the slowest). 280 iPhone photos took about 30 seconds on an M4 Pro. Analysed photos
  are cached, so re-reviewing a folder is instant.
- **Memory:** photos are decoded a few at a time and turned into small grayscale copies straight
  away. How many are processed in parallel depends on your RAM (2 on an 8 GB Mac, up to 8 on
  24 GB+), so other apps stay usable. A 48 MP HEIC needs a few hundred MB while it is open.
- **Disk:** the analysis cache is about 1 KB per photo. `copy` duplicates your photos, so the
  dry run shows how much space it needs and Pickwise refuses to start a copy that would leave
  less than 1 GB free; `move` and `xmp` need no extra space.
- **Model cost:** the result sent to Hermes is capped (about 40 photos per pile, the rest is in
  the report), so a 3,000-photo folder costs about the same tokens as a 300-photo one. The
  visual second look checks 6 photos by default.
- **Battery:** analysing thousands of photos keeps the CPU busy for a few minutes; plug in for
  big jobs.

## Privacy, safety and disclosure

- **Analysis is local.** Pickwise itself makes no network calls, no model calls, runs no shell
  commands and sends no telemetry. All measuring is done with Pillow on your computer.
- **Only previews you ask about leave your computer.** When you request a visual second look,
  Hermes' own vision tool sends those downsized previews to your model provider. Pickwise and
  its instructions tell Hermes never to do this unasked.
- **Reads** only the folder you name (subfolders only if you ask), and only image files;
  symlinks and hidden files are skipped.
- **Writes** nothing until you confirm a dry run of `pickwise_apply`. Then only inside a
  `Pickwise/` folder in the reviewed folder, or `.xmp` files next to photos that have none. If
  one file fails, the others still go through and are recorded, so `pickwise_undo` can always
  put moved photos back. No photo is ever deleted, overwritten or edited.
- **Stores** in Hermes' plugin data folder (`~/.hermes/plugin-data/pickwise-photos/`): per-photo
  measurements (sharpness, brightness, a 64-bit image hash, capture time, file path), the last
  10 reviews and 20 reports, your learned preferences and corrections, and, only when you ask
  for a visual check, downsized previews of the chosen photos (the last three sets).
- **Unattended runs** (cron, messaging gateway): nothing prompts or waits, and nothing is
  written without `confirm: true`.

## Troubleshooting

- **"Which folder?"** Write the full path in your message, e.g. `~/Pictures/trip`. In
  Finder, Option + Command + C copies a folder's path.
- **After `hermes plugins update pickwise-photos`**, run `hermes gateway restart` so the
  running gateway loads the new code.
- **Installing from inside a Hermes chat**: add `--yes-deps` so the dependency question is
  answered up front.
- **Vision check fails with error 429**: your model provider is rate-limiting image requests.
  Wait a while, or set a fast vision model (see the tip above).
- **Ratings don't show in Lightroom for JPEG/HEIC**: expected, see "Where the results go".
- **RAW-only shoot looks less precise**: check the "Analysed size" column in the report. If it
  is small (around 1.7 MP), your camera embeds a small preview; shoot RAW + JPEG for best
  results.
- **A big folder says "in progress"**: normal for thousands of photos. Say "continue" (or
  Hermes will) and it picks up where it stopped; nothing is analysed twice.

## Limitations

- Pickwise's own analysis judges focus, exposure and duplicates. It cannot see eyes,
  expressions or composition, and cannot tell whether focus landed on the person or on the
  background (a sharp tree behind a soft face reads as "sharp"). The visual second look covers
  this for the photos you choose.
- Scores close together are close calls: within a burst, the "best" frame may be only a point
  better than the next. Check by eye when the moment matters.
- Very noisy night shots can look sharper than they are. RAW files are judged from their
  embedded preview, which reflects the camera's own processing.
- It is not an editor and not a judge of style: it never retouches, crops or rates artistic
  choices.

## Roadmap

Ideas for later versions, not features of this one: an option to remember consent for the
visual check, side-by-side comparison of near-duplicates, and support for photo libraries
such as Apple Photos.

## Development

```bash
python -m pytest                                    # unit tests (they generate their own photos)
hermes plugins validate . --install-deps            # catalog admission checks
hermes plugins doctor . --ci                        # real runtime registration check
```

## License

MIT
