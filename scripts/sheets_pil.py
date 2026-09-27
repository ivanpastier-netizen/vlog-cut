#!/usr/bin/env python3
"""sheets_pil.py <clips_chrono.json> <outdir> [thumbs_per_clip]

Labelled contact sheets built with PIL instead of ffmpeg filtergraphs.

Use this rather than an ffmpeg drawtext sheet: Homebrew ffmpeg is often built WITHOUT
libfreetype, so `drawtext` does not exist and any labelled tile silently fails
("No such filter: 'drawtext'"). PIL also lets a cell hold several frames, which
matters — one frame per clip cannot tell you where the action is, and the whole
point of the sheet is picking an in-point ON the action.

Each cell = N frames spread across the clip + a burned-in label
(index, weekday, time of day, duration, source).
"""
import json, subprocess, sys, math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

clips = json.load(open(sys.argv[1]))
OUT   = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
NTH   = int(sys.argv[3]) if len(sys.argv) > 3 else 3
FR    = OUT/"frames"; FR.mkdir(exist_ok=True)

TW, TH = 300, 169
LAB    = 22
CW, CH = TW*NTH, TH+LAB
COLS, ROWS = 3, 4
DAY = {"01":"MON","02":"TUE","03":"WED","04":"THU","05":"FRI","06":"SAT","07":"SUN"}

def _font(sz):
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Supplemental/Verdana Bold.ttf"):
        try: return ImageFont.truetype(f, sz)
        except Exception: pass
    return ImageFont.load_default()
FONT = _font(15)

def cell(c):
    frames = []
    for k in range(NTH):
        t = max(0.05, c["dur"] * (k + 0.5) / NTH)
        p = FR/f"_c{c['idx']:03d}_{k}.jpg"
        if not p.exists():
            subprocess.run(["ffmpeg","-y","-v","error","-ss",f"{t:.2f}","-i",c["path"],
                            "-frames:v","1","-vf",f"scale={TW}:-2","-q:v","5",str(p)],
                           capture_output=True)
        if p.exists(): frames.append(p)
    if not frames: return None
    img = Image.new("RGB", (CW, CH), "black")
    for i, p in enumerate(frames):
        try: th = Image.open(p).convert("RGB")
        except Exception: continue
        th.thumbnail((TW, TH))
        img.paste(th, (i*TW + (TW-th.width)//2, LAB + (TH-th.height)//2))
    d = ImageDraw.Draw(img)
    # Two schemas reach this script and they are NOT interchangeable:
    #   probe.py        -> t="20260101_095218", cam="insta"
    #   other importers -> ts="2026-01-01T09:52:18", src="insta"
    # Indexing the compact form with ISO offsets silently yields "_0.8. :" — the
    # label goes blank and the sheet loses the one thing it is for (knowing WHICH
    # clip and WHEN). Normalise instead of assuming.
    raw = c.get("ts") or c.get("t") or ""
    dig = "".join(ch for ch in raw if ch.isdigit())
    when = f"{dig[6:8]}.{dig[4:6].lstrip('0')}. {dig[8:10]}:{dig[10:12]}" if len(dig) >= 12 else ""
    src = (c.get("src") or c.get("cam") or "").upper()
    bits = f" {c['bits']}b" if c.get("bits") else ""
    lab = (f"c{c['idx']:03d}   {when}   {c['dur']:.1f}s"
           + (f"   [{src}{bits}]" if src else ""))
    d.rectangle([0,0,CW,LAB], fill=(0,0,0))
    d.text((6,3), lab, fill=(255,220,0), font=FONT)
    return img

cells = [(c, cell(c)) for c in clips]
print(f"{sum(1 for _,i in cells if i)}/{len(cells)} cells", flush=True)

per = COLS*ROWS
for s in range(math.ceil(len(cells)/per)):
    grp = cells[s*per:(s+1)*per]
    sheet = Image.new("RGB", (CW*COLS, CH*ROWS), (20,20,20))
    for k,(c,im) in enumerate(grp):
        if im: sheet.paste(im, ((k%COLS)*CW, (k//COLS)*CH))
    p = OUT/f"sheet{s:02d}.jpg"
    sheet.save(p, quality=72, optimize=True)
    print(f"{p}  {','.join('c%03d'%c['idx'] for c,_ in grp)}", flush=True)
