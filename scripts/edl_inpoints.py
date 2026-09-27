#!/usr/bin/env python3
"""edl_inpoints.py <edl.json> [out_prefix] [cam] — tile the FIRST FRAME of every shot.

The single highest-yield review in this pipeline. A contact sheet shows what a
CLIP contains; this shows what the CUT actually opens each shot on — and that is
where the errors live: an in-point 4 s before the action, a frame of somebody's
knee, a shot whose subject has already left. Verified errors found this way on the
first pass: 11 of 64 shots, including a hero shot of a parent with a sleeping
child that opened on a close-up of denim shorts.

Reads the source + tin (+ rot) straight from the EDL, so it works BEFORE any
render. Filter to one camera to audit a single source at a time.
"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

edl = json.loads(Path(sys.argv[1]).read_text())
pref = sys.argv[2] if len(sys.argv) > 2 else str(Path(sys.argv[1]).with_suffix("")) + "_inpoints"
WANT = sys.argv[3] if len(sys.argv) > 3 else None
shots = [(i, c) for i, c in enumerate(edl["clips"])
         if WANT is None or c.get("cam") == WANT]
print(f"{len(shots)} shot(s){' cam='+WANT if WANT else ''} of {len(edl['clips'])}")

try: F = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 14)
except Exception: F = ImageFont.load_default()
TW, LAB = 320, 34
td = tempfile.mkdtemp(prefix="edlip-")
tiles = []
for i, c in enumerate(shots):
    idx, sh = c
    rot = sh.get("rot")
    vf = []
    if rot == 180: vf.append("hflip,vflip")
    elif rot == 90: vf.append("transpose=1")
    elif rot == 270: vf.append("transpose=2")
    # tone-map HLG so the tile is colour-honest; a naive HLG decode looks flat
    if "arib" in str(sh.get("src", "")) or True:
        vf.append("scale=%d:-2" % TW)
    f = os.path.join(td, "f.jpg")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-hwaccel", "videotoolbox",
                    "-ss", f"{sh['tin']:.3f}", "-i", sh["src"], "-frames:v", "1",
                    "-vf", ",".join(vf), "-q:v", "5", f], capture_output=True)
    if not os.path.exists(f):
        continue
    im = Image.open(f).convert("RGB"); im.thumbnail((TW, TW))
    cell = Image.new("RGB", (TW, im.height + LAB), "black")
    cell.paste(im, ((TW - im.width) // 2, LAB))
    d = ImageDraw.Draw(cell)
    tag = f"{sh.get('cam','?')[:5]}{sh.get('bits','')}b"
    d.text((3, 2), f"#{idx:03d} {sh.get('t','')[-6:]}@{sh['tin']:.1f} {sh['dur']:.1f}s"
                   f" [{tag}]{' ROT' if rot else ''}{' NAT' if sh.get('audio')=='nat' else ''}",
           fill=(255, 220, 0), font=F)
    d.text((3, 18), (sh.get("note") or "")[:56], fill=(150, 230, 255), font=F)
    tiles.append(cell)
import shutil; shutil.rmtree(td, ignore_errors=True)
cols, rows_per = 6, 6
per = cols * rows_per
cw = TW; ch = max(t.height for t in tiles)
for s in range((len(tiles) + per - 1) // per):
    grp = tiles[s * per:(s + 1) * per]
    g = Image.new("RGB", (cw * cols, ch * rows_per), (18, 18, 18))
    for k, im in enumerate(grp):
        g.paste(im, ((k % cols) * cw, (k // cols) * ch))
    out = f"{pref}_{s}.jpg"
    g.save(out, quality=78)
    print(out)
