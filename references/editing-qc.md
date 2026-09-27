# Editing, rendering and QC

Use this reference for shot selection, EDL work, music, review renders, revisions and QC.

## The loop

```bash
cp "$VLOG_SKILL/scripts/edl_template.py" "$D/edl_<project>.py"   # this file IS the edit
python3 "$VLOG_SKILL/scripts/music.py" usage
python3 "$VLOG_SKILL/scripts/music.py" find --bpm 90-110 --dur 150+ --unused
python3 "$D/edl_<project>.py"                                      # -> edl_<project>.json
python3 "$VLOG_SKILL/scripts/preview.py" "$D/edl_<project>.json" "$D/review.mp4" "$D/work" 1280
python3 "$VLOG_SKILL/scripts/qc.py" \
  "$D/edl_<project>.json" "$D/review.mp4" "$D/work/shots" "$D/transcripts.json"
```

If the user keeps private notes or a diary about the shoot day, they may inform what the day
was about, but never quote private material into a vlog, title or caption.

## Music

The library is a plain folder (`music_lib/tracks/`) with `catalog.json` beside it, built by
`music.py index`. The catalog carries a precomputed beat grid per track (`bpm`,
`beat_period`, `beats`) and an energy profile `prof` (RMS in 8 s buckets), so an EDL reads it
directly. `music.py usage` is a register of prior selections derived from local EDLs.

- For a new vlog choose an unused track by default. Reuse only when asked or when no suitable
  unused track exists, and say so before rendering.
- `find --bpm` also matches half and double tempo; `--exact-bpm` turns that off.
- `find --intro` prefers tracks whose first ~16 s sit well under the body — a free build for
  an act opener. That also means `mstart=0` on such a track is near-silence: `mstart` belongs
  in the body of the track. Print the energy at `mstart` on every build and warn under ~0.45
  of the track maximum.
- Music should already run under the cold open: shift the first act's track in-point back by
  the cold-open length so the act still lands on its beat.
- One track per roughly 60–90 s, not one per act. An act exists so the music can change; a
  single song split across acts jumps to a different point at every seam. Under ~2 min use one.
- Only use music you are licensed to use, and credit artists as their license requires.

## Editorial defaults

- Loosely chronological, with the strongest moment as the cold open.
- Narration target is roughly 25–30 %. Never narrate what the picture already shows.
- No explanatory voice-over while food is being made; use music and the real sound.
- End on picture, not a spoken summary.
- Cut throat-clearing openers, but do not micro-chop pauses inside one hesitant sentence —
  drop the line if it cannot play naturally. Drop lines the film says twice.
- A spoken setup must be followed by its visible payoff, preferably from the same clip and in
  real-time order. Grep the transcripts for imperatives and promises ("look", "jump", "watch
  this") and check the next 1–3 shots actually show it. A line repeated several times in a
  transcript is the tell that the payoff happens later in the take.
- Mine long takes: a 30 s+ clip is a scene, not a shot. Flag any clip over ~20 s used once and
  pull 2–4 shots from different beats of it.
- Mundane sequences get shorter through more, faster cuts — not by deleting coverage. At high
  BPM `b=3` (~1 s) is the montage pulse; reserve 6–8 beats for payoffs.
- Pick in-points on the action (the espresso pouring, not the empty cup). Contact-sheet labels
  are not proof the chosen in-point contains the subject.
- Speed-ups for process shots are constant (`speed=4.0` over a long span), not ramped; a ramp
  is a movement tool (whip pan, walking through a door).
- Real moments play at 1× with music ducked ~15 dB. Camera audio near −45 dBFS is wind, not
  speech — let the music carry it.
- Share cut: about 60 s, one track, music-led, ~1.1–1.3 s average shot.
- Trip outputs share one selection/EDL: daily 1–2 min cuts, a ~5 min vlog and a longer
  15–25 min archive cut with more natural-sound moments.
- Title cards need an outline, not a drop shadow: in HLG the card white is capped (~75 %) and
  cannot out-brighten a bright scene. Check each card against the brightest frame it covers.

## Hard edit invariants

- **No rewinds.** Adjacent shots from the same source must not overlap: beat-snapped
  durations can end past the next shot's in-point and replay footage. The template pushes
  overlaps ≤ 3 s forward; larger backward jumps are deliberate and need a shot from another
  clip between them. `qc.py`'s rewind check is a hard fail.
- **No rolled words.** A beat-snapped `nat` shot can run into the next sentence, which the
  following shot then repeats. Always pass transcripts to `qc.py`; the EDL emits `adur` so the
  audio ends where the sentence ends while the picture holds to the beat. Because `d=` snaps
  up, shortening it moves the out-point down a whole beat.
- **Cut on word boundaries.** Use Whisper word timestamps, never segment bounds. `cuts.py`
  takes a phrase and returns `(tin, adur)` placed in the silence between words (half the gap,
  capped at 0.35/0.40 s).
- **A beat is not a duration.** Clamp seconds, not beat counts (`while b > 2 and b*period >
  2.6: b -= 1`). Print a duration histogram after every build; one dominant value is a clamp bug.
- **Never mute a visibly talking face** — lips over music read as a sync error. Run
  `mutedspeech.py`; shift `tin` into silence or make the shot `nat`.
- **A garbled transcript is not unintelligible audio.** Never mute a shot because ASR failed;
  the original audio invents nothing. Whisper also hallucinates stock phrases over silence.
- Resolve multi-wave/day shots by capture time. After a migration, diff `(src, offset, tin,
  dur)` against the previous EDL.
- Never remove an EDL block by matching a non-unique comment string. Anchor structural edits
  to a unique act variable, keep a backup, rebuild and compare totals.
- Consecutive shots from the same static clip less than ~3 s apart read as a stutter, except
  in deliberate action beats.

## Loudness

`preview.py` runs two-pass EBU R128 `loudnorm` (I=-17, TP=-1.5, LRA=11) on natural sound.
Never normalise from mean volume alone: speech has a quiet mean and loud peaks, so a mean-
derived gain clips. If you add a manual gain, cap it at `-1.0 - max_volume`.

## Cache rules

`preview.py` caches segments by shot index. Use `work/` for review and `work4k/` for the
master; bitrate and resolution live in the segments, so a change needs a fresh workdir. A
reordered/added/removed shot list invalidates the whole cache. For trim-only edits remove the
affected `sNNN.mp4` and stale `nat/*.wav` / `mus/*.wav` stems with `find ... -delete` (a zsh
no-match glob aborts a multi-glob `rm` before deleting anything).

## QC gate

No PASS means no distribution. Run the complete `qc.py` battery and inspect the images. It
covers black/blown frames and exact per-segment/cumulative frame counts, muted speaking faces
(advisory), natural-audio alignment by correlation (`synccheck.py`), video alignment against
source frames (`avcheck.py`), rewind/overlap and split-word boundaries, container
packet/frame counts and final loudness.

Low-confidence correlation is `unknown`, not a failure: a detector that cannot express
uncertainty manufactures false certainty. When changing any detector, verify a known-good file
and a deliberately perturbed negative control (e.g. copy the EDL, `clips[-1]["tin"] += 1.0`,
and require the reported magnitude and direction to match).

The render is the authority. Extract the first frame of every rendered shot
(`checkshots.py`) and look at it; contact sheets and ladders can disagree with the actual cut.
A lens flare can read mostly white while measuring under 1 % blown — the number points, the
eye decides.

## Review hand-off and revisions

After QC PASS, verify the review with `ffprobe`, copy it to `review_dir`, run `review_hook`
if configured, and write `<state_dir>/pending.json`:

```json
{"status": "awaiting-review", "project": "...", "edl": "...", "workdir": "...",
 "review_sent": "2026-01-01T18:00:00", "planned_masters": ["4K", "1080p"]}
```

Stop there until the user approves. For feedback, read `pending.json`, edit the same
`edl_*.py`, invalidate only the necessary caches, re-render, re-run QC and resend. Before
finals, check for newer project mtimes and logs — another session may be working on it.
