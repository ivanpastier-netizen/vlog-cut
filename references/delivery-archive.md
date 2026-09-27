# Delivery, publication and archive

Use this only after the user approves the review, or for an explicitly requested replacement
of an already approved cut.

## Concurrency check

Read `<state_dir>/pending.json`, then compare `review_sent` with project mtimes and render/QC
logs. Fresh finals on disk are not proof of distribution; the `distributed` status is. If
another session is active, verify its artifacts and resume from the first missing stage
instead of rendering or importing twice.

## Export ladder — render once, derive the rest

| # | What | How |
| --- | --- | --- |
| 1 | 4K working master | `preview.py <edl> master_4k.mp4 <abs work4k> 3840 80M` — HEVC Main10 HLG or H.264 SDR per colour mode |
| 2 | 4K delivery `.m4v` | from ①, ~28 Mbit/s. SDR: `h264_videotoolbox -b:v 28M -maxrate 34M -bufsize 50M -c:a copy`. HLG: `hevc_videotoolbox -b:v 28M -profile:v main10 -tag:v hvc1 -colorspace bt2020nc -color_primaries bt2020 -color_trc arib-std-b67 -pix_fmt p010le -c:a copy -f mp4` |
| 3 | 1080p compatibility | from ①, always SDR BT.709: `scale=1920:1080:flags=lanczos -c:v libx264 -preset slow -crf 19 -c:a copy`; from an HLG master prepend the HLG→709 mapping |
| 4 | 720p review / messaging copy | from ③, 2-pass `libx264 -b:v 1550k` + 128k AAC, SDR, under ~50 MB |

- ① needs its own workdir. Segments are cached as `<workdir>/shots/sNNN.mp4` with no width in
  the name; pointing a 3840 render at the review workdir silently reuses 1280 segments and
  ships an upscaled 720p film labelled 4K.
- A `.m4v` name selects the ipod muxer, which cannot hold HEVC — the error surfaces misleadingly
  on the filter and leaves a 0-byte file. Add `-f mp4` and keep the name.
- Use libx264 when squeezing to a size target, VideoToolbox when you need speed.
- `-c:a copy` everywhere below the master; never re-encode the finished mix.
- Before delivery verify duration, frame count ≈ `duration × fps`, colour tags, mean loudness
  near −18 dB and max/true peak below −1 dB.

## Destinations

Configure where finals go; typical choices:

- a local "Vlogs" folder and/or a NAS share (4K + 1080p only — not proxies or sheets, which
  belong in a separate footage tree so media servers do not index them);
- Apple Photos (import the 4K delivery with the shoot date so it sorts correctly);
- a small project-documents folder in cloud storage: EDL, rebuild script, transcripts, README —
  not the masters.

Apple Photos cannot delete items by script. For a replacement, retitle the old item as
superseded, import the new one with the same shoot date, and ask the user to delete the old
item. Imported filenames become UUID-backed; verify the new asset by querying Photos, not by
original filename.

If you keep a catalog of published vlogs, publishing is incomplete until its record is updated.
Any derived catalog file must be regenerated, never hand-edited.

## Availability is three-state

A file is present locally, present on a reachable archive host, or **unknown** because the
host is unreachable. Only "host reachable and absent from both" is a missing-file failure.
Off-network is uncertainty, not loss.

## Transfer integrity

- Use SSH (`rsync -e ssh` or `tar | ssh`), not an SMB mount, for bulk archive transfers.
- `rsync` on a directory without `-r` transfers zero files and still exits 0; with `-q` you
  see nothing. Always `-rv`.
- Old rsync versions split a remote destination path containing spaces into several
  arguments and still exit 0. Transfer to a space-free name, then `ssh mv`.
- Compare local `md5 -q` with remote `md5sum` for every material file:

```bash
for f in *.json *.mp4; do
  L=$(md5 -q "$f"); R=$(ssh "$ARCHIVE_HOST" "md5sum '$DEST/$f'" | cut -d' ' -f1)
  [ "$L" = "$R" ] || echo "MISMATCH $f"
done
```

Never delete local staging until the archive copy is checksum-verified and at least one other
copy of the originals still exists.

## Self-contained source bundle

The irreplaceable artifact is not the render but a source bundle: the raw clips used,
`edl.json`, a self-locating rebuild script, clip and transcript indexes and a README, with
music referenced from a backed-up library. Prove the archived rebuild script runs on the
archive host and every EDL source path resolves; only then consider cleaning local staging.

## Compilations

Build compilations (e.g. "best of the year") by filtering archived selections without
modifying originals. Anything the user has marked private or restricted stays out unless they
explicitly clear it for that compilation.
