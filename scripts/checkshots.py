#!/usr/bin/env python3
"""checkshots.py <edl.json> <shots_dir> — audit rendered segments before shipping.

Three checks per shot, one global:
  * exists and decodes;
  * luminance — near-black / blown-out frames;
  * FRAME-EXACT length — the segment must hold exactly round(dur×29.97) frames.
    This is the check that matters most: cutting segments by time (-t) lets the
    fps filter drop the last frame on ~1 in 4 shots. 33 ms each — invisible to
    any per-shot duration tolerance — but across 104 concatenated shots it
    compounded to 767 ms of A/V drift by the END of one cut. Sync complaints
    that grow toward the end of the film are THIS, not the mix (synccheck.py
    validates the audio side and cannot see it).
  * cumulative video deficit — must be 0 frames; anything else re-renders the
    short segments (preview.py now encodes with -frames:v, so a short segment
    means the source ran out — reshoot the EDL window, don't ship it).
"""
import json, subprocess, sys, re
import numpy as np
from pathlib import Path

FR = 1001/30000

def main():
    e = json.load(open(sys.argv[1]))
    SH = Path(sys.argv[2])
    bad, deficit = [], 0
    for i, c in enumerate(e["clips"]):
        p = SH/f"s{i:03d}.mp4"
        if not p.exists() or p.stat().st_size < 3000:
            bad.append((i, c["name"], "MISSING/EMPTY")); continue
        exp = max(1, round(c["dur"]/FR))
        r = subprocess.run(["ffprobe","-v","error","-count_packets","-select_streams","v:0",
                            "-show_entries","stream=nb_read_packets","-of","csv=p=0",str(p)],
                           capture_output=True, text=True)
        got = int(re.sub(r"[^0-9]","", r.stdout) or 0)
        if got != exp:
            deficit += exp-got
            bad.append((i, c["name"], f"FRAMES {got} != {exp} ({(exp-got)*FR*1000:+.0f} ms)"))
        r = subprocess.run(["ffmpeg","-v","error","-i",str(p),"-vf","fps=4,scale=32:18,format=gray",
                            "-f","rawvideo","-"], capture_output=True)
        a = np.frombuffer(r.stdout, dtype=np.uint8)
        if a.size < 32*18:
            bad.append((i, c["name"], "NO FRAMES")); continue
        mean = a.reshape(-1,18,32).astype(float).mean()
        if mean < 12:   bad.append((i, c["name"], f"NEAR BLACK mean={mean:.1f}"))
        elif mean > 245: bad.append((i, c["name"], f"BLOWN OUT mean={mean:.1f}"))
    print(f"checked {len(e['clips'])} shots — problems: {len(bad)}")
    for i, n, w in bad: print(f"  s{i:03d} {n[:46]:46} {w}")
    print(f"cumulative video deficit: {deficit} frames = {deficit*FR*1000:.0f} ms "
          f"{'✅' if deficit==0 else '⚠ THE TIMELINE WILL DRIFT — fix before mux'}")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
