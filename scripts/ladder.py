#!/usr/bin/env python3
"""ladder.py <clip.mp4> <t0> <t1> <step> <out.png> — time-based frame ladder.

⛔ -nostdin is not optional: without it ffmpeg eats stdin, so a shell loop
`while read f; do ladder.py "$f" ...; done < list.txt` silently loses lines
(measured: 4 of the first 6 clips skipped, one with a half-eaten filename).

⛔ NEVER build a ladder with select='eq(n,<t*29.97>)'. The GoPro proxies are
59.94 fps, so a frame number computed at 29.97 lands at HALF the intended time —
every label lies by 2x and every in-point taken from it is wrong. Seek by TIME.
"""
import subprocess, sys, os
from PIL import Image, ImageDraw

clip, t0, t1, step, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
td = out + ".d"; os.makedirs(td, exist_ok=True)
HLG_MAP = ("hwupload,scale_vt=color_matrix=bt709:color_primaries=bt709:color_transfer=bt709,"
           "hwdownload,format=p010le,format=yuv420p")
# an SDR source (GoPro 8-bit Rec.709, or a Rec.709 proxy) has nothing to tone-map, and the
# p010le hwdownload REFUSES 8-bit frames -> ffmpeg errors, no png is written, and the script
# exits "no frames". Detect the transfer and skip the HLG path.
_tr = subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries",
                      "stream=color_transfer","-of","default=nw=1:nk=1", clip],
                     capture_output=True, text=True).stdout.strip()
MAP = HLG_MAP if _tr in ("arib-std-b67","smpte2084") else "format=yuv420p"
ts, fs = [], []
t = t0
while t < t1 - 1e-6:
    f = f"{td}/{t:07.2f}.png"
    r = subprocess.run(["ffmpeg","-nostdin","-v","error","-init_hw_device","videotoolbox=vt",
                        "-filter_hw_device","vt","-ss",f"{t:.3f}","-i",clip,"-frames:v","1",
                        "-vf",f"{MAP},scale=300:-2","-y",f], capture_output=True)
    if os.path.exists(f):
        ts.append(t); fs.append(f)
    t += step
if not fs:
    sys.exit("no frames")
# ⛔ Cap frames per sheet. A 59 s clip at 0.25 s is 236 frames — on one sheet the
# thumbnails come out unreadable and the review is worthless. Split into parts.
PER = 64
cols = 8; W = 300; H = Image.open(fs[0]).height; LAB = 15
parts = [(fs[i:i+PER], ts[i:i+PER]) for i in range(0, len(fs), PER)]
for pi,(pf,pt) in enumerate(parts):
    rows = (len(pf)+cols-1)//cols
    s = Image.new("RGB", (cols*W, rows*(H+LAB)), (12,12,12)); d = ImageDraw.Draw(s)
    for i,(f,tt) in enumerate(zip(pf, pt)):
        im = Image.open(f); x=(i%cols)*W; y=(i//cols)*(H+LAB)
        s.paste(im,(x,y+LAB)); d.text((x+3,y+2), f"{tt:.1f}s", fill=(255,215,0))
    o = out if len(parts)==1 else out.replace(".jpg", f"_p{pi+1}.jpg").replace(".png", f"_p{pi+1}.png")
    s.save(o, quality=85)
    print(o, s.size, f"{len(pf)} frames {pt[0]:.1f}-{pt[-1]:.1f} s")
print(f"total {len(fs)} frames, {len(parts)} sheet(s), step {step}")
