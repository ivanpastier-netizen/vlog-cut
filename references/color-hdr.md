# Colour, HDR and exposure

Read this when the ingest has HDR, mixed bit depth, mixed cameras or night footage. The
numbers below were measured on real footage through `preview.py`'s actual filter chain;
re-measure if you change the colour pipeline.

## Capture: one shared colour language

Modern phones and action cameras record HLG (often Dolby Vision profile 8.4: HEVC Main10,
HLG transfer, BT.2020). Keep it — it plays natively in Photos and editors and gives HDR
headroom. On mirrorless cameras choose an **HLG** movie mode rather than a flat log profile
(log is meant for grading). With every camera in neutral HLG, residual mismatch between
brands is white balance, not gamma.

Framerate: capture at 59.94 where possible — a 29.97 timeline takes every second frame
(clean cadence) and you get 2× slow-motion headroom. Avoid 50/25 in a 29.97 timeline
(irregular cadence, judder on pans) and watch for stray 48 fps modes. In 50 Hz mains
countries set anti-flicker to 50 Hz when shooting 60 indoors.

## Colour modes in preview.py

`preview.py` resolves and prints a colour mode per render:

- **hlg** — most sources are HDR (or the EDL sets `"color": "hlg"`): masters are HEVC Main10
  BT.2100 HLG, `-tag:v hvc1`. HLG clips pass through; SDR/PQ stragglers are mapped into HLG.
  Title cards drop to ~75 % white (HLG diffuse white) so they do not glare at peak nits.
- **sdr** — H.264 BT.709; HDR stragglers are mapped down (otherwise they ship washed out).
  SDR renders are tagged `-colorspace bt709 -color_primaries bt709 -color_trc bt709`
  explicitly — otherwise they inherit the HLG source's BT.2020 matrix.

Both mappings run in VideoToolbox via `scale_vt`. To *look at* an HLG render on an SDR screen
(frame grabs, sheets), map it first — a naive decode of HLG always looks flat:

```
-vf "hwupload,scale_vt=color_matrix=bt709:color_primaries=bt709:color_transfer=bt709,hwdownload,format=p010le,format=yuv420p"
```

The measuring chain needs `-init_hw_device videotoolbox=vt -filter_hw_device vt`, and
`format=yuv420p10le` before a second `hwupload`. `-hwaccel videotoolbox` on proxy passes is
~3.3× faster than software decode of 4K 10-bit HEVC with identical output.

## Matching cameras: native by default

- **Single-camera day → zero trims.** `ev` is a camera-matching tool only. "Correcting" a
  camera's own auto-exposure within one camera usually makes it worse.
- Globally calibrating every camera to the dullest one washes the whole film out. Keep cameras
  native; trims are scene tools.
- **Exposure is the one thing that does need matching across cameras.** Median native luma can
  differ by more than a stop between brands, which makes the picture flicker on every camera
  change. Solve it per camera from `proxy_index.py`'s `luma_native`/`ev_suggest`.
- Some cameras run hotter saturation (~1.5×); `"sat": 0.75` on those clips is a typical tame.
- Intercutting two cameras in the same scene and it shows? Measure the **same materials**
  (face, shirt) across cameras, not abstract neutrals. `cammatch.py` automates it; apply ~¼ of
  the computed linear correction and iterate — the view tone-map amplifies trims 3–4×.
- A measurement is only valid **at the in-point you actually use**; auto-exposure drifts
  within a clip.
- Trim knobs on any EDL clip: `wb [rr,bb]`, `ev` stops, `sat`, `con` (+`pivot`), `rot`.

## Grade for skin, not frame mean

On a backlit shot the frame mean is dominated by the window, so a "too bright, pull it down"
rule darkens the face. A blown sky costs little; a dark face costs the shot. A well-exposed
Rec.709 face sits around luma ~150.

Skin can be estimated with a YCbCr mask (`133≤Cr≤173`, `77≤Cb≤127`, `40<Y<250`, median of
masked pixels) — but it is a **hint, not a setpoint**:

- Under ~10 % coverage it measures the bright background and keeps reporting "too dark", so
  chasing it overshoots.
- It also matches amber food and drinks. Apply it only to shots that contain a person.
- Real corrections are small (−0.15…+0.3 EV).
- Look at a rendered, view-mapped frame before sending anything. Every number here can be
  satisfied by a visibly wrong picture.

## `ev` is a plain ratio

`ev = log2(target / measured)` — not scaled by a 2.2 gamma; the HLG→709 view transform is not
a power curve. Measured ladder on a dark macro shot (native luma 58): `ev 0.00 → 56.9 · 0.50 →
80.5 · 0.71 → 93.1 · 1.10 → 122.0 · 1.55 → 166.4`. The gamma model would have asked for 1.55
and blown the film.

## Clipping: sweep, read the median

Sampling three frames and reporting the maximum wildly overstates clipping. `proxy_index.py`
sweeps 10 points and stores `blown_median` (is it really blown?) and `blown_peak` (moments to
avoid when picking the in-point):

- **Transient** (handheld swings past a window): fix by choosing the in-point.
- **Persistent** (static shot into a window): no grade recovers it; never lift an already
  clipped shot.

## Trims must run in float RGB

`colorlevels` renders pure black on 10-bit planar input (`gbrp10le`, `yuv420p10le`); `exposure`
masked it by forcing a float conversion, so it bit only on shots with `con` and no `ev`.
`preview.py` emits `format=gbrpf32le` ahead of the trim block. Nothing upstream warns — the QC
near-black check is what catches this class of bug.

## SDR sources inside an HLG render

- 8-bit sources must be converted with `format=yuv420p10le` **first**, or `hevc_videotoolbox`
  fails with `-22 Invalid argument` and writes a 0-byte segment that a naive "skip existing
  segments > 2 KB" cache would slide past.
- The correct reference is the clip's **HLG neighbours**, not its own SDR appearance. Measured:
  native 10-bit HLG 119.1, mapped 8-bit 97.8 → `ev ≈ 0.23` lands within 0.1 luma of native. So
  a mixed-bit-depth day ships as HDR with every clip in; set `"color": "hlg"` and put `ev` on
  the SDR ones.
- **Test SDR by transfer characteristic, not bit depth.** Some cameras fall back to 10-bit
  Rec.709 in low light. Two targets:

| Source | ev | Goal |
| --- | --- | --- |
| 8-bit Rec.709 | 0.23 | sit with HLG neighbours |
| 10-bit Rec.709 | 0.50 | return to its own native level |

```python
HDR_TRC = ("arib-std-b67", "smpte2084")     # anything else is SDR
if c.get("transfer") not in HDR_TRC:
    rec["ev"] = EV8BIT if c.get("bits") == 8 else EVSDR
```

- Those defaults were measured on daylight clips. On night scenes the SDR→HLG mapping loses
  shadow detail; a per-shot `ev` around 0.85 can be right. Put it on the shot, never globally.
  Measure paired frames (`source@tin+o` vs `render@offset+o`), exclude fades, use ≥ 6 frames of
  a moving scene — and treat shadow statistics as a trigger to look, not a verdict.

`preview.py` prints a warning listing every SDR source in an HLG render — read it.

## `-f null` DTS warnings are not corruption

`ffmpeg -i f -f null -` can print "non monotonically increasing dts to muxer" on perfectly
intact files — that is the null muxer complaining on the way out. Judge a copy on real decode
failures:

```python
DECODE_ERR = ("corrupt", "error while decoding", "invalid data", "no frame",
              "could not find codec", "truncat")
p = run(["ffmpeg", "-nostdin", "-v", "error", "-i", f, "-f", "null", "-"])
bad = (p.returncode != 0
       or any(k in p.stderr.lower() for k in DECODE_ERR)
       or count_frames(f) != header_frames(f))
```

Then prove the loosened check still bites: intact → PASS, truncated to 60 % → FAIL, a few bytes
of garbage → FAIL.

## `adur`: the escape hatch for off-grid cut points

A sentence must end where speech ends; the picture should end on a beat. When no beat lands in
the silence, `d=` alone cannot satisfy both (it snaps up). Set `adur` to end the audio in the
silence and let the picture hold to the next beat. Stutters are where Whisper is least
reliable; re-transcribing a short window does not fix it — say the take is unconfirmed rather
than guessing.
