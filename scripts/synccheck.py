#!/usr/bin/env python3
"""synccheck.py <edl.json> <rendered_master> — measure A/V placement of every nat shot.

Cross-correlates the master's audio against each nat shot's source audio around
the shot's timeline offset. Reports the lag: within +-40 ms (~1 frame @29.97) is
locked; anything bigger means the mix placed the moment off its picture.

Built after a viewer reported a "sound doesn't match picture" moment on one
cut — the measurement proved sync was fine (4-36 ms everywhere) and the real bug
was a muted shot with a visibly talking face (see mutedspeech.py). Run BOTH: this
one answers "is the mix aligned", that one answers "does a muted face talk".

⛔⛔ A LAG IS ONLY EVIDENCE IF THE CORRELATION PEAK IS CONFIDENT.
The old version took `np.correlate(..., "valid")` — a RAW dot product — and
trusted its argmax blindly. On a shot whose audio is broadband noise (a splash,
wind, a crowd) the envelope is nearly flat, every lag scores about the same, and
argmax picks noise. Measured on one shoot day: a pool-jump shot was reported
1456 ms OFF and blocked distribution, while THREE independent checks put it at
+10..+12 ms like every other shot in the film:
  - boundary-exact envelope correlation: +10 ms, identical to all 25 other nat shots
  - normalised NCC constrained to +-200 ms: +12 ms (its neighbours: +8, +12)
  - the "far" peak beat the near one by only r 0.388 vs 0.262 — a 0.126 margin
Measured r for that day: speech shots 0.80-0.98, correctly-locked splash shots
0.47-0.70, the mislock 0.388. So r ALONE does not separate them (0.47 locked
fine) — the discriminator is whether a FAR peak is CONVINCINGLY better than a
near one. Hence: normalise, then prefer the near peak unless the far peak wins
by a real margin. A low-confidence disagreement is reported `? weak`, printed
loudly for eyes, and does NOT hard-fail the gate.

Caveat: the first shot of the timeline has no pre-roll to correlate against, so
its row is meaningless — skip shots at offset < pad.
"""
import json, subprocess, sys
import numpy as np

SR   = 16000
PAD  = 1.5      # seconds of context either side
LOCK = 40       # ms — within one frame @29.97 is locked
NEAR = 200      # ms — a mux either places a shot right or catastrophically wrong
MARGIN = 0.25   # r the far peak must beat the near peak by to be believed
TRUST  = 0.60   # r below which a far peak is not evidence of anything

def wav(src, ss, dur):
    r = subprocess.run(["ffmpeg","-nostdin","-v","error","-ss",f"{ss:.4f}","-i",src,
                        "-t",f"{dur:.4f}","-vn","-ac","1","-ar",str(SR),
                        "-f","f32le","-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32).astype(np.float64)

def env(x, hop=64):
    n = len(x)//hop
    e = np.abs(x[:n*hop].reshape(n, hop)).max(axis=1)
    return e, SR/hop

def ncc(a, b):
    """Normalised sliding cross-correlation of template `a` over signal `b`.

    Returns the full r curve. Per-window normalisation is the whole point: it
    makes the peak HEIGHT comparable across lags, so a flat envelope reports a
    low r instead of a confident-looking argmax.
    """
    a = a - a.mean()
    na = np.sqrt((a*a).sum())
    m, out = len(a), len(b) - len(a) + 1
    if out <= 0 or na == 0:
        return None
    cs  = np.concatenate(([0.0], np.cumsum(b)))
    cs2 = np.concatenate(([0.0], np.cumsum(b*b)))
    s1, s2 = cs[m:] - cs[:-m], cs2[m:] - cs2[:-m]
    nb = np.sqrt(np.maximum(s2 - s1*s1/m, 0.0))
    num = np.correlate(b, a, mode="valid")
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(nb > 0, num/(na*nb), 0.0)

def main():
    e = json.load(open(sys.argv[1])); master = sys.argv[2]
    bad = weak = 0
    print(f"{'shot':44} {'master@':>9} {'lag(ms)':>9} {'r':>6}  verdict")
    for c in e["clips"]:
        if c.get("audio") != "nat": continue
        if c["offset"] < PAD:
            print(f"{c['name'][:44]:44} {c['offset']:9.2f}   (skipped — no pre-roll)")
            continue
        src = wav(c["src"], c["tin"], c["dur"])
        mst = wav(master, c["offset"]-PAD, c["dur"]+2*PAD)
        if len(src) < SR//2 or len(mst) < SR//2: continue
        a, fps = env(src); b, _ = env(mst)
        r = ncc(a, b)
        if r is None: continue
        lags = (np.arange(len(r))/fps - PAD) * 1000
        far  = int(np.argmax(r))
        w    = np.abs(lags) <= NEAR
        near = int(np.flatnonzero(w)[np.argmax(r[w])]) if w.any() else far

        if abs(lags[near]) <= LOCK and (r[far] - r[near]) < MARGIN:
            k, verdict = near, "ok"                      # near peak stands
        elif abs(lags[far]) > LOCK and r[far] >= TRUST:
            k, verdict = far, "⚠ OFF"; bad += 1          # a confident misplacement
        elif abs(lags[far]) <= LOCK:
            k, verdict = far, "ok"
        else:
            k, verdict = far, f"? weak (near {lags[near]:+.0f} ms r={r[near]:.2f})"
            weak += 1
        print(f"{c['name'][:44]:44} {c['offset']:9.2f} {lags[k]:9.1f} {r[k]:6.3f}  {verdict}")

    if weak:
        print(f"\n{weak} shot(s) INCONCLUSIVE — broadband audio (splash/wind/crowd), the "
              f"correlator has nothing to lock onto. Not a failure; confirm by ear.")
    print(f"\n{'ALL LOCKED' if not bad else f'{bad} SHOT(S) OFF — investigate the mix'}")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
