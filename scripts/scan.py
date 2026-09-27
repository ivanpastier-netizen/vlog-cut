#!/usr/bin/env python3
"""scan.py <clips_chrono.json> <idx> [n=8] [out.jpg] — in-point picker grid.

Samples n frames evenly across one clip so you can choose WHERE the action is
(the espresso pouring, not the empty glass). Fast NAIVE decode on purpose —
fine for judging content and motion, meaningless for judging color (HLG decoded
naively always looks flat/dark; color is judged on rendered frames only).
"""
import os, subprocess, json, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw

clips = {c["idx"]: c for c in json.load(open(sys.argv[1]))}
idx = int(sys.argv[2])
n = int(sys.argv[3]) if len(sys.argv) > 3 else 8
out = sys.argv[4] if len(sys.argv) > 4 else f"scan_c{idx:03d}.jpg"
c = clips[idx]; step = c["dur"] / (n + 1)
# a FIXED /tmp path makes two concurrent scans overwrite each other's frame —
# the second grid silently shows the first clip's picture. Use a private file.
_tmpdir = tempfile.mkdtemp(prefix="vlogscan-")
FRAME = os.path.join(_tmpdir, "_scan.jpg")
tiles = []
for k in range(n):
    t = step * (k + 1)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-hwaccel", "videotoolbox",
                    "-ss", f"{t:.2f}", "-i", c["path"], "-frames:v", "1",
                    "-vf", "scale=300:-2", "-q:v", "5", FRAME], check=True)
    img = Image.open(FRAME); d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 80, 16], fill=(0, 0, 0)); d.text((4, 2), f"{t:.1f}s", fill=(255, 255, 0))
    tiles.append(img.copy())
w, h = tiles[0].size; rows = (n + 3) // 4
grid = Image.new("RGB", (w * 4, h * rows))
for k, im in enumerate(tiles): grid.paste(im, ((k % 4) * w, (k // 4) * h))
grid.save(out)
print(f"{out}  ({c['file']}, {c['dur']:.0f}s, step {step:.1f}s)")
import shutil; shutil.rmtree(_tmpdir, ignore_errors=True)
print(out)
