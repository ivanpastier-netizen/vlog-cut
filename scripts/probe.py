#!/usr/bin/env python3
"""probe.py <media_dir> [out.json] — build clips_chrono.json from an ingest dir.

Every downstream tool (sheets_pil, scan, transcribe, the creative EDL) reads
this file. Camera is inferred from the filename prefix:
  VID_    -> insta      FUJI_ / DSCF -> fuji      IPHONE_ / IMG_ -> iphone
Chronology comes from the filename timestamp (YYYYMMDD_HHMMSS) when present —
container creation_time on a copied file is the COPY date, don't trust it.
Incremental: re-run any time; it rebuilds from what's on disk (cheap).
"""
import subprocess, json, re, sys
from pathlib import Path

# ⛔ RESOLVE. `path` lands in clips_trip.json and from there in the EDL's `src`,
# which preview.py hands to ffmpeg VERBATIM — so a relative path only renders
# from the one cwd it happened to be created in. Measured case: a day was first
# probed as `probe.py /abs/path` (absolute srcs, rendered fine), then re-probed
# as `probe.py .` when late clips landed — every src in the trip index turned
# relative and the next render died with "Error opening input file
# IPHONE_20260101_120000_clean.mov".
D = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else D / "clips_chrono.json"

def cam_of(name):
    n = name.upper()
    if n.startswith(("VID_", "INSTA")): return "insta"
    if n.startswith(("FUJI", "DSCF")): return "fuji"
    # GoPro video prefixes: GX=HEVC, GH=AVC, GS=Max/360, GOPR=legacy. Chaptered
    # files are GX<chapter><number>.MP4 and carry NO date in the name — chronology
    # for those comes from creation_time, so photos_pull renames them GOPRO_<ymd>_<hms>.
    if n.startswith(("GOPRO", "GX", "GH", "GS", "GOPR")): return "gopro"
    if n.startswith(("IPHONE", "IMG_")): return "iphone"
    return "unknown"

clips = []
for f in sorted(D.iterdir()):
    if f.suffix.lower() not in (".mp4", ".mov") or f.name.startswith("."): continue
    if cam_of(f.name) == "unknown": continue          # skip renders/derived files
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,color_transfer,"
        "codec_name,pix_fmt,color_primaries,color_space:"
        "format=duration:format_tags=creation_time", "-of", "json", str(f)],
        capture_output=True, text=True)
    j = json.loads(r.stdout); st = j["streams"][0]; fm = j["format"]
    m = re.search(r"(\d{8})_(\d{6})", f.stem)
    ts = f"{m.group(1)}_{m.group(2)}" if m else fm.get("tags", {}).get("creation_time", "")[:19]
    num, den = map(int, st["r_frame_rate"].split("/"))
    pix = st.get("pix_fmt") or ""
    clips.append(dict(file=f.name, path=str(f), cam=cam_of(f.name), t=ts,
                      dur=round(float(fm["duration"]), 3), fps=round(num / den, 3),
                      w=st["width"], h=st["height"],
                      transfer=(st.get("color_transfer") or "").rstrip(","),
                      codec=st.get("codec_name") or "", pix_fmt=pix,
                      bits=10 if "10" in pix else (12 if "12" in pix else 8),
                      primaries=(st.get("color_primaries") or "").rstrip(","),
                      space=(st.get("color_space") or "").rstrip(",")))
clips.sort(key=lambda c: c["t"])
for i, c in enumerate(clips): c["idx"] = i
json.dump(clips, open(OUT, "w"), indent=1)
for c in clips:
    print(f"c{c['idx']:03d} {c['cam']:6} {c['t'][-6:]}  {c['dur']:7.1f}s  "
          f"{c['w']}x{c['h']}@{c['fps']:.5g}  {c['codec']} {c['bits']}bit "
          f"{c['transfer'] or 'bt709'}")
print(f"{len(clips)} clips, {sum(c['dur'] for c in clips)/60:.1f} min -> {OUT}")
from collections import Counter
mix = Counter((c["cam"], c["bits"], c["transfer"] or "bt709") for c in clips)
print("\ncamera / bit depth / transfer census:")
for (cam, bits, tr), n in sorted(mix.items()):
    print(f"  {cam:6} {bits}bit {tr:14} {n:4d} clips")
if len({b for _, b, _ in mix}) > 1:
    print("  NOTE: MIXED bit depth in this day — 8-bit clips are Rec.709 SDR and")
    print("        must be mapped INTO HLG by preview.py, not passed through.")
