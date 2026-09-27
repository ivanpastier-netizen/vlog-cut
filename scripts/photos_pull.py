#!/usr/bin/env python3
"""photos_pull.py <YYYY-MM-DD> <outdir> — pull that day's videos out of Apple
Photos into the ingest dir (a phone lands there via iCloud; a dedicated camera
lands there when it is imported while connected in PTP mode).

- Reads Photos.sqlite via immutable URI (no 600 MB copy). The immutable view
  lags very recent imports by a minute or two — re-run if something's missing.
- Camera identity from the ORIGINAL's streams: pcm_s24le audio = a dedicated
  camera, anything else = phone (its DV 8.4 + apac spatial track).
- Remuxes to a single video+audio stream (`_clean.mov`) — a phone's spatial
  audio + mebx tracks and a camera's tmcd track break editing tools and other tooling.
- Skips files already pulled AND any asset whose UUID is listed in
  <outdir>/photos_exclude.txt (one UUID per line — our own rendered
  tests/masters imported back into Photos must not re-enter the ingest).
"""
import subprocess, sys, sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vlog_config as C

day = sys.argv[1]
out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
LIB = C.path("photos_library")
if not LIB:
    raise SystemExit("photos_library is not set in vlog-cut config")
excl = set()
xf = out / "photos_exclude.txt"
if xf.exists():
    excl = {l.strip() for l in xf.read_text().splitlines() if l.strip()}

def cam_of(src, orig=""):
    """Camera identity — the ORIGINAL FILENAME first, then the file's own tags.

    The camera writes its own filename, and that prefix is the one thing that is
    never absent: some 360 cameras ship clips with NO Make/Model at all, so
    both the tag rule and the audio fallback below silently call it "IPHONE".
    Measured case: a full day's clips from such a camera all came through
    labelled IPHONE, which would have poisoned a multi-camera exposure match
    (and invited an iPhone-specific exposure correction onto the wrong footage).
    probe.py already keys camera off exactly this prefix, so trusting it here
    keeps the two halves of the pipeline from disagreeing. A tag that contradicts
    the prefix is printed, never silently preferred.
    """
    n = (orig or "").upper()
    byname = ("INSTA" if n.startswith("VID_")
              else "FUJI" if n.startswith(("DSCF", "FUJI")) else
              "GOPRO" if n.startswith(("GOPR", "GX", "GH", "GS")) else
              "IPHONE" if n.startswith(("IMG_", "IPHONE")) else "")
    m = subprocess.run(["exiftool", "-s3", "-Make", "-Model", "-CompressorName",
                        str(src)], capture_output=True, text=True).stdout.upper()
    bytag = ("GOPRO" if ("GOPRO" in m or "HERO" in m)
             else "FUJI" if "FUJIFILM" in m
             else "INSTA" if ("INSTA360" in m or "ARASHI" in m)
             else "IPHONE" if ("APPLE" in m or "IPHONE" in m) else "")
    if not bytag:
        pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "stream=codec_name", "-of", "csv=p=0", str(src)],
                            capture_output=True, text=True).stdout
        bytag = "FUJI" if "pcm_s24le" in pr else ""
    if byname and bytag and byname != bytag:
        print(f"  cam: {orig} prefix says {byname}, tags say {bytag} -> using {byname}")
    return byname or bytag or "IPHONE"

db = sqlite3.connect(f"file:{LIB}/database/Photos.sqlite?immutable=1", uri=True)
rows = db.execute(
    "SELECT a.ZUUID, a.ZFILENAME, a.ZDIRECTORY, "
    "strftime('%H%M%S', a.ZDATECREATED+978307200, 'unixepoch', 'localtime'), "
    "aa.ZORIGINALFILENAME "
    "FROM ZASSET a LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON aa.ZASSET=a.Z_PK "
    "WHERE a.ZTRASHEDSTATE=0 AND a.ZKIND=1 AND "
    "date(a.ZDATECREATED+978307200,'unixepoch','localtime')=? "
    "GROUP BY a.ZUUID ORDER BY a.ZDATECREATED",
    (day,)).fetchall()
ymd = day.replace("-", "")
smap = out / "photos_source_map.tsv"
seen = set()
if smap.exists():
    seen = {l.split("\t")[0] for l in smap.read_text().splitlines() if l.strip()}
n_new = 0
with smap.open("a") as mf:
    if not seen:
        mf.write("uuid\toriginal\tdest\tsrc_bytes\tcam\n")
    for uuid, fn, d, hms, orig in rows:
        if uuid in excl:
            continue
        src = LIB / "originals" / str(d) / fn
        if not src.exists():
            print(f"MISSING original (iCloud not downloaded?): {orig or fn}"); continue
        cam = cam_of(src, orig or fn)
        dst = out / f"{cam}_{ymd}_{hms}_clean.mov"
        # two clips can share a second (GoPro chapters, burst) — never clobber
        k = 1
        while dst.exists() and uuid not in seen:
            dst = out / f"{cam}_{ymd}_{hms}_{k}_clean.mov"; k += 1
        if uuid in seen and dst.exists():
            continue
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                        "-map_metadata", "-1", "-map_chapters", "-1", str(dst)],
                       check=True)
        mf.write(f"{uuid}\t{orig or fn}\t{dst.name}\t{src.stat().st_size}\t{cam}\n")
        n_new += 1
        print(f"pulled {dst.name}  <- {orig or fn} ({cam})")
print(f"done — {n_new} new clip(s); source map -> {smap}")
print("re-run probe.py after any pull")
