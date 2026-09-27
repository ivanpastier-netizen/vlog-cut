#!/usr/bin/env python3
"""qc.py <edl.json> <master> <shots_dir> [transcripts.json] — the ship gate.

Runs the whole QC battery in one command and prints a PASS/FAIL scorecard.
Nothing distributes without a PASS. Individual checks live in their own
scripts (checkshots / synccheck / avcheck / mutedspeech); this only sequences
them and adds the container + loudness + toolchain checks.

Exit 0 = PASS. Exit 1 = at least one hard check failed.
mutedspeech is advisory (needs eyes on the triage sheet), it never fails the gate.
"""
import json, subprocess, sys, re
from pathlib import Path

HERE = Path(__file__).resolve().parent

def sh(args):
    return subprocess.run(args, capture_output=True, text=True)

def main():
    edl, master, shots = sys.argv[1], sys.argv[2], sys.argv[3]
    transcripts = sys.argv[4] if len(sys.argv) > 4 else None
    e = json.load(open(edl))
    hard_fail = []

    ver = sh(["ffmpeg", "-version"]).stdout.splitlines()[0].split()[2]
    print(f"ffmpeg {ver}  (known-good: 8.1 + preview.py's segment-scoped title mux — "
          f"re-run this battery after ANY ffmpeg upgrade)")

    # 1. segments: frame-exact + luminance
    r = sh([sys.executable, str(HERE/"checkshots.py"), edl, shots])
    ok = "deficit: 0 frames" in r.stdout and "problems: 0" in r.stdout
    print(f"[{'PASS' if ok else 'FAIL'}] shots            {r.stdout.splitlines()[-1] if r.stdout else r.stderr[-120:]}")
    if not ok: hard_fail.append("shots")

    # 1b. ⛔ REWIND: two neighbouring shots from the same clip whose in-points
    # overlap play a chunk of material TWICE. A viewer described it exactly:
    # "a couple of shots seemed to rewind a few tenths of a second?" Measured
    # on real projects: over a dozen such overlaps across three days
    # (0.13-2.67 s), plus a couple of backward jumps of 17 and 29 s. No other
    # check catches this — the render is frame-exact, audio lines up, duration
    # matches. This checks the EDL itself, not the rendered master.
    cl = e["clips"]
    laps, jumps = [], []
    for i in range(len(cl) - 1):
        a, b = cl[i], cl[i+1]
        if a["t"] != b["t"]:
            continue
        aout = a["tin"] + a["dur"] * a.get("speed", 1.0)
        if b["tin"] < aout - 1e-6:
            ov = aout - b["tin"]
            (jumps if ov > 4.0 else laps).append((i, a["t"][-6:], round(a["tin"], 2),
                                                  round(aout, 2), round(b["tin"], 2), round(ov, 2)))
    ok = not laps
    print(f"[{'PASS' if ok else 'FAIL'}] rewind           "
          + ("no overlapping in-points" if ok
             else f"{len(laps)} shot(s) play material twice"))
    for i, t, ai, ao, bi, ov in laps:
        print(f"       #{i}->{i+1} {t}: {ai}-{ao}s, then from {bi}s  => {ov}s played twice")
    if jumps:
        print(f"       [LOOK] {len(jumps)} backward jump(s) within a clip (>4 s) — "
              f"may be intentional, check: " + ", ".join(f"#{i}->{i+1} {t} -{ov}s" for i,t,_,_,_,ov in jumps))
    if not ok: hard_fail.append("rewind")

    # 1c. ⛔ A WORD SPLIT AT A SHOT BOUNDARY. A nat `d=` gets ROUNDED UP to the
    # nearest beat, so a shot ends LATER than intended and its tail already
    # starts the next sentence — which then REPEATS from the start in the
    # following shot. It sounds like a skipping record. It is not a rewind
    # (the in-points don't overlap), so the check above misses it, the render
    # is frame-exact, synccheck reports 'locked', and the duration matches.
    # Measured on real cuts: a `d=1.10` shot actually ended at 1.47 s and cut a
    # sentence in half mid-word; a `d=2.60` shot ended at 2.94 s and repeated
    # the first words of the next sentence. Only this check catches either.
    if transcripts:
        try:
            tx = json.load(open(transcripts))
        except Exception:
            tx = {}
        segs = {}
        for fn, t in tx.items():
            parts = fn.split("_")
            if len(parts) > 2:
                segs[parts[2]] = (t.get("segments") if isinstance(t, dict) else t) or []
        split = []
        for i in range(len(cl) - 1):
            a, b = cl[i], cl[i+1]
            if a["t"] != b["t"] or a.get("audio") != "nat" or b.get("audio") != "nat":
                continue
            for sg in segs.get(a["t"][-6:], []):
                # the cut lands INSIDE a sentence: it starts before the boundary
                # and still has >50 ms to run after it
                if sg["start"] < b["tin"] - 0.05 < sg["end"] - 0.05:
                    split.append((i, a["t"][-6:], round(b["tin"], 2),
                                  round(sg["start"], 2), round(sg["end"], 2),
                                  sg["text"].strip()[:52]))
                    break
        ok = not split
        print(f"[{'PASS' if ok else 'FAIL'}] split word       "
              + ("no cut lands inside a sentence" if ok
                 else f"{len(split)} cut(s) land inside a sentence — will play twice"))
        for i, t, bnd, s0, s1, txt in split:
            print(f"       #{i}->{i+1} {t}: cut at {bnd}s breaks sentence {s0}-{s1}s  \"{txt}\"")
            print(f"       fix: shorten the previous `d=` so it ends before {s0}s "
                  f"(remember, d= rounds UP to the beat)")
        if not ok: hard_fail.append("split-word")

    # 2. container: exact frame count vs the EDL grid
    exp = round(e["total"] * 30000 / 1001)
    n = int(re.sub(r"[^0-9]", "", sh(["ffprobe","-v","error","-count_packets",
        "-select_streams","v:0","-show_entries","stream=nb_read_packets",
        "-of","csv=p=0",master]).stdout) or 0)
    ok = n == exp
    print(f"[{'PASS' if ok else 'FAIL'}] container        {n}/{exp} frames"
          + ("" if ok else "  <- mux dropped frames (ffmpeg 8 framesync race?)"))
    if not ok: hard_fail.append("container")

    # 3. loudness
    det = sh(["ffmpeg","-i",master,"-af","volumedetect","-f","null","-"]).stderr
    mean = maxv = None
    for L in det.splitlines():
        if "mean_volume:" in L: mean = float(L.split("mean_volume:")[1].split("dB")[0])
        if "max_volume:"  in L: maxv = float(L.split("max_volume:")[1].split("dB")[0])
    ok = mean is not None and -24 < mean < -14 and maxv < -0.5
    # The printed target MUST match the condition above. This drifted once
    # ("want < -1" printed against a real threshold of maxv < -0.5), so a
    # render at max -0.9 dB passed while LOOKING like it broke the limit —
    # a reader then "corrects" a non-bug.
    print(f"[{'PASS' if ok else 'FAIL'}] loudness         mean {mean} dB "
          f"(want -24..-14), max {maxv} dB (want < -0.5)")
    if not ok: hard_fail.append("loudness")

    # 4. nat audio placement (cross-correlation)
    r = sh([sys.executable, str(HERE/"synccheck.py"), edl, master])
    ok = "ALL LOCKED" in r.stdout
    print(f"[{'PASS' if ok else 'FAIL'}] audio sync       {'all nat shots locked' if ok else (r.stdout.splitlines()[-1] if r.stdout else r.stderr[-120:])}")
    if not ok: hard_fail.append("audio-sync")

    # 5. video-side alignment (frame-diff sweep) — needs nat shots in the film's
    # second half; a cut without them (tiny tests) is a SKIP, not a failure.
    r = sh([sys.executable, str(HERE/"avcheck.py"), edl, master])
    if not r.stdout.strip():
        print("[SKIP] video sync       no late nat shots to sweep")
    else:
        ok = "ALIGNED" in r.stdout and "behind" not in r.stdout and "ahead" not in r.stdout
        print(f"[{'PASS' if ok else 'FAIL'}] video sync       " +
              ("last nat shots aligned" if ok else r.stdout.strip().splitlines()[-1]))
        if not ok: hard_fail.append("video-sync")

    # 6. muted talking faces — advisory, needs eyes
    if transcripts:
        r = sh([sys.executable, str(HERE/"mutedspeech.py"), edl, transcripts,
                str(Path(master).resolve().parent/"qc_muted")])
        tail = [l for l in r.stdout.splitlines() if l.strip()]
        print(f"[LOOK] muted faces      {tail[-2] if len(tail)>=2 else '(no output)'}")
        print(f"       triage sheet:    {tail[-1].split(': ',1)[-1] if tail else '-'}")

    print("=" * 60)
    if hard_fail:
        print(f"QC FAIL: {', '.join(hard_fail)} — do not distribute")
        return 1
    print("QC PASS — ok to distribute (after eyes on the triage sheet + a real watch)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
