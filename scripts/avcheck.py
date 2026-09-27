#!/usr/bin/env python3
"""Video-side A/V proof: for late nat shots, the master's frame at (offset+k)
must match the SOURCE frame at (tin+k). If the video timeline drifted, the
master frame matches an EARLIER source time instead. Reports, per shot, which
source-time offset best matches the master frame — 0.0 s = aligned.

⛔⛔ COMPARE STRUCTURE, NOT BRIGHTNESS, AND SAY WHEN YOU ARE UNSURE.
The first version diffed RAW luma means between the master and the source. That
silently assumes the master is an ungraded copy of the source — which it is not:
`ev` lifts a shot, and an SDR source is mapped 709->HLG. Measured on a real
concert coda (the first time the LAST shot carried `ev`): master luma 139.5
vs source 121.2 (exactly what ev 0.50 + the HLG map does), and the distances came
out best=21.86 at lag +1.0 against 22.14 at lag 0.0 — a 1.3 % spread over a
21.86..34.45 range, i.e. NO minimum at all. It reported "video behind by 1.0s"
and failed a correct master. The tell: the winning lag sat on the search
BOUNDARY, which is what "no minimum inside the window" always looks like.

Three fixes, all needed:
  1. z-normalise each frame (subtract mean, divide by std) before diffing, so a
     brightness/contrast change cannot dominate a temporal comparison.
  2. sample BEFORE the fade-out. The final shot is faded to black, so a late
     sample compares a darkened frame against full-brightness sources.
  3. report confidence. If the best lag is not meaningfully better than lag 0,
     or it lands on the search boundary, say `? weak` and do NOT fail the gate.
A detector that cannot express uncertainty manufactures false certainty, and a
false FAIL stops a finished film to hunt a bug that is not there.
"""
#   avcheck.py <edl.json> <master.mp4> [n] — check the LAST n late nat shots (default 3)
import json, subprocess, sys
import numpy as np
from PIL import Image

e = json.load(open(sys.argv[1]))
MASTER = sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 3
FADE = float(e.get("fade_out") or 0)
# ⛔ The window must be WIDER than any error worth reporting, or a real error
# lands on the boundary and the edge rule below dismisses it as "no minimum".
# Caught by the negative control: shifting a tin by exactly 1.0 s against a
# +-1.0 s window put the true minimum ON the edge. +-2.0 s keeps a 1 s error
# comfortably interior.
LAGS = np.arange(-2.0, 2.01, 0.1)
EDGE = 2.0
MARGIN = 0.04   # the best lag must beat lag 0 by this fraction to be believed

def grab(src, t, out):
    subprocess.run(["ffmpeg","-nostdin","-y","-v","error","-ss",f"{t:.3f}","-i",src,
                    "-frames:v","1","-vf","scale=320:-2","-q:v","4",out],
                   capture_output=True)
    try: return np.asarray(Image.open(out).convert("L"), dtype=float)
    except Exception: return None

def z(a):
    """Structure only: kill the brightness offset and the contrast scale, which is
    what `ev` and the 709<->HLG map change and what alignment does NOT."""
    s = a.std()
    return (a - a.mean())/s if s > 1e-6 else a - a.mean()

nats = [c for c in e["clips"] if c["audio"] == "nat" and c["offset"] > e["total"]*0.5]
bad = weak = 0
for idx, c in enumerate(nats[-N:]):
    # keep the sample clear of the fade-out on the film's last shot
    last = (c is e["clips"][-1])
    lim = c["dur"] - FADE - 0.10 if (last and FADE > 0) else c["dur"]*0.6
    k = max(0.15, min(2.0, c["dur"]*0.6, lim))
    m = grab(MASTER, c["offset"]+k, "/tmp/avcheck_m.jpg")
    if m is None: continue
    mz = z(m)
    res = []
    for lag in LAGS:
        s = grab(c["src"], c["tin"]+k+lag, "/tmp/avcheck_s.jpg")
        if s is None or s.shape != m.shape: continue
        res.append((float(np.abs(z(s)-mz).mean()), float(lag)))
    if not res: continue
    res.sort()
    best_d, best = res[0]
    d0 = min(d for d, l in res if abs(l) < 0.05)
    spread = res[-1][0] - best_d
    t = c["offset"]
    edge = abs(abs(best) - EDGE) < 1e-6
    rel = (d0 - best_d)/d0 if d0 > 0 else 0.0

    if abs(best) <= 0.15:
        verdict = "ALIGNED"
    elif rel < MARGIN or edge or spread < 1e-6:
        verdict = (f"? weak (lag 0 is {rel*100:.1f}% worse"
                   + (", winner sits on the window edge" if edge else "") + ") — not scoring")
        weak += 1
    else:
        verdict = f"video {'ahead' if best < 0 else 'behind'} by {abs(best):.1f}s"
        bad += 1
    print(f"{c['name'][:44]:44} @{int(t//60)}:{t%60:05.2f}  best source lag {best:+.1f}s  "
          f"-> {verdict}")
if weak:
    print(f"{weak} shot(s) inconclusive — flat or heavily graded picture, no minimum "
          f"in the search window. Not a failure.")
if not bad:
    print("ALIGNED")
