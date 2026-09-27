# Local workflow and project state

Use this reference for ingest, analysis and project continuation.

## Project layout

One shoot day = one project dir: `<ingest_root>/<YYYY-MM-DD>-ingest/`. Every script reads and
writes plain JSON beside the media:

| File | Written by | Meaning |
| --- | --- | --- |
| `clips_chrono.json` | `probe.py` | every clip: path, camera, capture time, duration, fps, colour transfer, bit depth |
| `transcripts.json`, `words/` | `transcribe.py` | segment text + per-word timestamps |
| `proxies/`, `thumbs/`, `shot_index.json` | `proxy_index.py` | 960px Rec.709 proxies, thumb strips, exposure metrics, human `desc`/`keep`/`tags` |
| `local_triage.json` | `local_triage.py` | advisory local-vision observations |
| `edl_<project>.py` → `.json` | you | the authored edit |
| `work/`, `work4k/` | `preview.py` | segment caches for review and master |

## New footage

For a single shoot day:

```bash
D=~/Movies/vlog-ingest/<YYYY-MM-DD>-ingest    # i.e. <ingest_root>/<date>-ingest from your config
mkdir -p "$D"
# 1. get the media in — one of:
python3 "$VLOG_SKILL/scripts/photos_pull.py" <YYYY-MM-DD> "$D"          # from Apple Photos
python3 "$VLOG_SKILL/scripts/card_resume_copy.py" --date <YYYYMMDD>      # from a camera card
#    ...or simply copy files into $D named <CAM>_<YYYYMMDD>_<HHMMSS>.<ext>
# 2. analyse
python3 "$VLOG_SKILL/scripts/probe.py" "$D"
python3 "$VLOG_SKILL/scripts/transcribe.py" "$D/clips_chrono.json" "$D/transcripts.json"
python3 "$VLOG_SKILL/scripts/proxy_index.py" "$D"
python3 "$VLOG_SKILL/scripts/sheets_from_proxies.py" "$D" 3
python3 "$VLOG_SKILL/scripts/local_triage.py" "$D"     # optional, batches over ~20 clips
python3 "$VLOG_SKILL/scripts/vlog_status.py" "$D"
```

`probe.py` infers the camera from the filename prefix and takes chronology from the
`YYYYMMDD_HHMMSS` in the name — a copied file's container `creation_time` is often the copy
date. It stores absolute paths: the EDL hands them to ffmpeg verbatim.

For a flaky card mount use `card_resume_copy.py`: it resumes, filters by capture date (a card
is a rolling archive holding unrelated shoots) and dedupes by timestamp plus duration, not
filename.

## Incremental day rule

During the day do order-independent work only: pull, probe, transcribe, proxy, contact
sheets, one-line descriptions and exposure measurements. Assemble once the day's footage is
complete — the strongest cold open often arrives late. Keep the story loosely chronological.

For multi-day trips, merge with `trip_index.py` and resolve shots by full capture-time key.
For a multi-wave single day use the stable `HHMMSS` key. Never hard-code a mutable clip index
in an EDL that may receive more footage.

## The shot library

A proxy is ~1.5 % of a 4K HLG original, so a sensible long-term policy is: **index + proxies
forever, raw originals on a rolling window** (pinned with `keep: true`). `shot_index.json`
merges the human fields `desc`, `keep`, `tags` across re-runs; everything else is re-derived.
`proxy_index.py --push` copies the library to `archive_host:archive_root` over SSH when
configured, and verifies it by MD5.

`proxy_index.py` checks every existing proxy's duration against the original: a size floor
cannot tell a finished proxy from one killed mid-transcode, and a truncated proxy silently
hides the second half of a clip.

## What requires eyes

Scripts locate blur, clipping, duplicates, speech and scene boundaries. Local vision can
cheaply propose visible settings, actions and framing issues, but its observations stay
untrusted until verified. Neither can decide whether a spoken setup is paid off, whether
eyes are closed, whether a stranger blocks the subject, or whether two good shots repeat the
same event. Inspect:

1. proxy contact sheets for every new clip;
2. dense ladders for action-camera clips;
3. first-frame sheets from the authored EDL;
4. shot sheets extracted from the finished render.

Fill `shot_index.json` descriptions from what is visible, never from filenames.

## Action-camera review

Review in three passes on proxies (never the 4K): 1.0 s to locate actions, 0.25 s within
the windows you will cut, 0.1 s only for suspect opening frames. Pack sheets full (8×8) —
one image costs the same with 8 or 64 frames. Use `ladder.py`: it seeks by time (computing
frame numbers on a 59.94 fps proxy lands at half the intended time), picks the right colour
map for 8-bit vs HLG sources and passes `-nostdin` so a shell loop does not skip clips.
Ladder the whole clip, both halves.

An action clip is an arc — approach, action, landing — not a random two-second fragment.
Static action-camera shots that hold a full cycle deserve 4–6 s holds among the 2 s beats.
A camera mounted upside down produces an inverted clip that can pass a contact sheet (it
reads as a reflection); EDL clips take `rot: 90|180|270`.

## Local dependency check

`vlog_status.py` with no arguments checks commands and configured inputs. Missing optional
items degrade the applicable branch and must not block unrelated work. An unreachable archive
host means "unknown / defer archive", not "file missing".
