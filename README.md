# vlog-cut

A [Claude Code](https://claude.com/claude-code) skill that edits your personal or family vlogs
locally on a Mac. You drop in the day's footage; Claude ingests it, transcribes it, looks at
contact sheets, writes a beat-aware edit decision list (EDL), renders a review copy, runs an
automated QC gate and stops for your approval before anything is published.

The editing style it is tuned for is fast-paced daily-vlog storytelling, in the spirit of
creators like Casey Neistat: a strong cold open, quick montage cuts on the beat, real moments
at natural speed with the music ducked, and very little narration. (A descriptive reference
only; this project is not affiliated with or endorsed by any creator.)

## What it does

1. **Ingest** — from Apple Photos, a camera card, or files you copy into a project folder.
2. **Analyse** — `ffprobe` census (camera, HDR/SDR, bit depth), local Whisper transcription
   with word timestamps, Rec.709 proxies, contact sheets, exposure/clipping metrics, optional
   local-vision pre-triage via Ollama.
3. **Select & edit** — Claude reads transcripts and labelled frames and writes
   `edl_<project>.py`: acts, shots, in-points, beat counts, natural-sound moments, music.
4. **Render** — `preview.py` renders the EDL with ffmpeg/VideoToolbox: HLG HDR or SDR,
   per-camera exposure matching, two-pass loudness, music ducking, title cards.
5. **QC** — frame counts, black/blown frames, audio/video sync, rewinds, words cut
   mid-sentence, muted talking faces, loudness. No PASS, no delivery.
6. **Review → publish** — a 720p review goes to your review folder; after you approve, the
   4K/1080p export ladder, optional archive with checksum verification.

It never deletes or modifies camera originals.

## Requirements

- macOS on Apple Silicon (rendering uses VideoToolbox; transcription uses `mlx-whisper`)
- [Claude Code](https://claude.com/claude-code)
- `ffmpeg` / `ffprobe` (`brew install ffmpeg`)
- Python 3.10+ with `Pillow` and `numpy` (`pip install Pillow numpy`)
- `mlx-whisper` for transcription (`pip install mlx-whisper`)
- Optional: Full Disk Access for the terminal (Apple Photos ingest reads the Photos library database read-only), [Ollama](https://ollama.com) with a vision
  model (pre-triage), `exiftool`, `yt-dlp` (music library tools), SSH access to a NAS/server
  (archive)

## Install

```bash
git clone https://github.com/ivanpastier-netizen/vlog-cut.git ~/.claude/skills/vlog-cut
# or copy the folder into ~/.claude/skills/vlog-cut
mkdir -p ~/.config/vlog-cut
cp ~/.claude/skills/vlog-cut/config.example.json ~/.config/vlog-cut/config.json
# edit paths; every key is optional
python3 ~/.claude/skills/vlog-cut/scripts/vlog_config.py      # show resolved config
python3 ~/.claude/skills/vlog-cut/scripts/vlog_status.py      # check the toolchain
```

## Configuration

Settings come from `~/.config/vlog-cut/config.json` (or the file in `$VLOG_CONFIG`), and any key
can be overridden with an environment variable `VLOG_<KEY>`:

| Key | Default | Purpose |
| --- | --- | --- |
| `ingest_root` | `~/Movies/vlog-ingest` | one `<YYYY-MM-DD>-ingest/` project dir per shoot day |
| `music_lib` | `~/Music/vlog` | `tracks/` + `catalog.json` with beat grids |
| `state_dir` | `~/.config/vlog-cut` | `pending.json` review hand-off |
| `review_dir` | `~/Desktop` | where review copies land |
| `review_hook` | — | command run with the review path, e.g. a script that sends it to your phone |
| `photos_library` | `~/Pictures/Photos Library.photoslibrary` | Apple Photos ingest |
| `card_volume` | — | camera card mount point |
| `whisper_python` / `whisper_model` | current python / `mlx-community/whisper-large-v3-mlx` | transcription |
| `language` | auto-detect | Whisper language code |
| `triage_model` | `gemma4:12b` | local Ollama vision model |
| `archive_host` / `archive_root` | — | optional SSH archive (`user@host`, path) |

## Music

Build a library of tracks you are licensed to use in `music_lib/tracks/`, then run
`python3 scripts/music.py index` to compute BPM, beat grids and energy profiles. Respect each
track's license and credit artists where required. If you use `music.py fetch`, only fetch
music whose license allows download and use in your videos.

## Example prompts

- "I uploaded today's videos to Photos — cut a vlog."
- "Make a 60-second share cut from yesterday's project."
- "Less narration in the middle, and the cold open should be the jump into the lake."
- "Approved — export the finals."
- "What's the state of the beach-day project?"

## Layout

```
SKILL.md                entry point Claude reads
references/             focused playbooks: workflow, editing & QC, colour/HDR, delivery, model routing
scripts/                probe, transcribe, proxies, sheets, triage, EDL template, render, QC, music
config.example.json     configuration template
```

## Privacy

Your footage, transcripts and config stay on your machine; nothing in the pipeline uploads
anything. `.gitignore` excludes media, work dirs, per-project JSON and your `config.json`. If
you fork this repo, keep your own EDLs and project notes out of it — they contain names and
places.

## License

MIT — see [LICENSE](LICENSE). Covers this skill's code and instructions only, not any music or
footage you process with it.
