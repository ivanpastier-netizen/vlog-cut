#!/usr/bin/env python3
"""clip_quality.py <trip-or-day-dir> [cam] — objective per-clip quality, off the proxies.

Action footage fails in ways a contact sheet hides: a water-blurred lens, motion
blur from a fast pan, a shot that never settles. Eyeballing 44 GoPro clips finds
the obvious ones and misses the marginal ones, so measure first and LOOK second.

Per clip, sampled over 9 frames of the Rec.709 proxy:
  sharp_med  variance of the Laplacian, median over frames — the standard focus
             /blur metric. LOW = soft: wet lens, motion blur, or out of focus.
  sharp_best the sharpest single frame — a clip whose median is low but whose best
             is high has USABLE moments; one where both are low has none.
  sharp_t    the timestamp of that best frame — where to put the in-point.
  motion     mean absolute frame-to-frame difference. HIGH = whip pan or chaos;
             on an action cam high motion usually means unusable, not exciting.
  usable     sharp_best relative to the camera's own median, so the threshold
             adapts per camera instead of being a magic number.

Writes quality.json beside the index and merges `sharp_*`/`motion` into
shot_index.json so the EDL and any later review can read them.
"""
import json, os, statistics as st, subprocess, sys, tempfile
from pathlib import Path

D = Path(sys.argv[1]).resolve()
WANT = sys.argv[2] if len(sys.argv) > 2 else None
IDX = D / "shot_index.json"
doc = json.loads(IDX.read_text())
clips = [c for c in doc["clips"] if (WANT is None or c["cam"] == WANT)]
print(f"{len(clips)} clip(s){' cam='+WANT if WANT else ''} in {D.name}")

def proxy_of(c):
    p = c.get("proxy_abs") or str(D / c["proxy"])
    return p if os.path.exists(p) else None

N = 9
td = tempfile.mkdtemp(prefix="cq-")
out = []
for k, c in enumerate(clips, 1):
    px = proxy_of(c)
    if not px:
        continue
    dur = c["dur"]
    frames = []
    for i in range(N):
        t = dur * (i + 0.5) / N
        f = os.path.join(td, f"f{i}.png")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", px,
                            "-frames:v", "1", "-vf", "scale=320:-2,format=gray", f],
                           capture_output=True)
        if r.returncode == 0 and os.path.exists(f):
            frames.append((t, f))
    if not frames:
        continue
    from PIL import Image
    import array
    sharp, prev, motion = [], None, []
    for t, f in frames:
        im = Image.open(f).convert("L")
        w, h = im.size
        px_ = list(im.getdata())
        # variance of a 4-neighbour Laplacian, sampled on a stride to stay cheap
        vals = []
        for y in range(1, h - 1, 2):
            row = y * w
            for x in range(1, w - 1, 2):
                i = row + x
                vals.append(4 * px_[i] - px_[i - 1] - px_[i + 1] - px_[i - w] - px_[i + w])
        sharp.append((t, st.pvariance(vals) if len(vals) > 1 else 0.0))
        if prev is not None:
            motion.append(sum(abs(a - b) for a, b in zip(px_[::7], prev[::7])) / len(px_[::7]))
        prev = px_
    svals = [s for _, s in sharp]
    best_t, best_s = max(sharp, key=lambda x: x[1])
    out.append(dict(t=c.get("t_full", c["t"]), day=c.get("day"), cam=c["cam"],
                    dur=round(dur, 2), desc=c.get("desc", ""), keep=c.get("keep"),
                    sharp_med=round(st.median(svals), 1), sharp_best=round(best_s, 1),
                    sharp_t=round(best_t, 2),
                    motion=round(st.median(motion), 2) if motion else None))
    if k % 10 == 0 or k == len(clips):
        print(f"  {k}/{len(clips)}", flush=True)
import shutil; shutil.rmtree(td, ignore_errors=True)

med_best = st.median([o["sharp_best"] for o in out]) if out else 0
for o in out:
    o["usable"] = round(o["sharp_best"] / max(med_best, 1e-9), 2)
(D / "quality.json").write_text(json.dumps(
    dict(dir=str(D), cam=WANT, n=len(out), median_sharp_best=round(med_best, 1),
         clips=out), indent=1, ensure_ascii=False))

by = {o["t"]: o for o in out}
for c in doc["clips"]:
    q = by.get(c.get("t_full", c["t"]))
    if q:
        for f in ("sharp_med", "sharp_best", "sharp_t", "motion", "usable"):
            c[f] = q[f]
IDX.write_text(json.dumps(doc, indent=1, ensure_ascii=False))

out.sort(key=lambda o: o["usable"])
print(f"\nmedian sharp_best = {med_best:.1f}   -> quality.json + merged into shot_index.json")
print(f"\n{'clip':22} {'dur':>6} {'s_med':>7} {'s_best':>7} {'at':>6} {'mot':>6} {'usable':>6}  desc")
for o in out:
    flag = "SOFT" if o["usable"] < 0.55 else ("ok" if o["usable"] < 1.1 else "SHARP")
    print(f"{o['t']:22} {o['dur']:6.1f} {o['sharp_med']:7.1f} {o['sharp_best']:7.1f} "
          f"{o['sharp_t']:6.1f} {str(o['motion']):>6} {o['usable']:6.2f} {flag:5} {o['desc'][:44]}")
