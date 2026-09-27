#!/usr/bin/env python3
"""EDL TEMPLATE — copy me into the ingest dir as edl_<project>.py and edit.

This file IS the edit. Everything else is machinery. What you decide here:
  * the STORY: which shots, in which order, grouped into acts
  * per shot: in-point (tin), length (b=whole beats for montage, d=seconds
    for real-audio "nat" moments), audio mode, per-shot ev override
  * per act: one music track + where in the track it starts (mstart)
  * TITLES: act cards
How to work:
  1. read transcripts.json -> find the lines worth keeping (nat moments)
  2. look at sheets + scan.py grids -> pick in-points ON the action
  3. write the shot lists below; run this file; render preview.py; LOOK; iterate
Rules that tend to survive review: mundane = fast (b=2-4); funny/real = nat
at 1x; cut the montage on beats; never mute a visibly talking face; a nat line
that runs to the clip's tail gets the WHOLE clip (whisper timestamps are coarse).
The example below is a fictional "morning coffee" day (a cold open on the pour,
then a walk montage) — replace it with your own shot list; the build() machinery
below it does not need to change.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vlog_config as C

TMP   = Path(__file__).resolve().parent
LIB   = C.MUSIC_LIB                                      # the library — FCP is not involved
MUSIC = LIB / "tracks"
OUT_JSON = TMP / (Path(__file__).stem + ".json")          # edl_<project>.py -> edl_<project>.json
FPS_N, FPS_D = 30000, 1001
FRAME = FPS_D / FPS_N

clips = {o["idx"]: o for o in json.load(open(TMP/"clips_chrono.json"))}
# beat grids come precomputed with the library; pick tracks with `music.py find`
beats = json.load(open(LIB/"catalog.json"))

def snap(t):
    return round(t / FRAME) * FRAME

# per-camera look matching (kitchen calibration; ev is per-scene)
# WIP v1 lesson: matching everything DOWN to the dullest camera washes a warm
# scene out — an untrimmed iPhone often looks best. Keep cameras native, only
# tame the outlier's saturation; the color differences between cameras can be
# part of the scene's character, not a defect to erase.
TRIM = {          # per-camera; per-shot ev/con/sat overrides go on S() rows.
  "iphone": dict(),                    # native — its look is baked in anyway
  "fuji":   dict(wb=[0.933,0.983]),    # slight cool-down; do NOT tame sat globally
  "insta":  dict(),                    # the reference look
}
# A dull underexposed macro shot can be brought back to life with ev=+1.1,
# con=1.15 at native saturation — punch per shot, not per camera.

ACTS = [
  dict(name="A1 coffee",
       track="example-track.mp3",  mstart=8.0,  gain=-8.0),
]

def S(i, in_, b=None, d=None, a="mute", speed=1.0, gain=None, ev=None, con=None,
      sat=None, rot=None, note=""):
    return dict(idx=i, tin=in_, beats=b, dur=d, audio=a, speed=speed, gain=gain,
                ev=ev, con=con, sat=sat, rot=rot, note=note)

# ---------------- COLD open — the coffee pour, natural sound ----------------
COLD = [
  S(9, 4.4, d=3.3, a="nat", ev=0.3, note="coffee pours into the cup — kitchen sound"),
]

# ---------------- A1 — morning walk montage ----------------
A2 = [
  S(6,  13.6, b=4, ev=1.0, note="door opens onto the street"),
  S(6,  28.0, b=4, ev=1.0, note="feet on the pavement"),
  S(7,  7.2,  b=4, ev=1.0, note="crossing the street"),
  S(7,  28.8, b=4, ev=1.0, note="hand trails along a railing"),
  S(9,  16.6, b=5, ev=0.3, note="turning a corner into the park"),
  S(9,  39.6, b=6, ev=0.3, note="path through the trees"),
  S(8,  0.0,  d=2.6, a="nat", ev=0.3, note="a passer-by says good morning"),
  S(10, 0.6,  b=4, note="light through the leaves"),
  S(11, 1.6,  b=6, note="steps up to the bench"),
  S(11, 5.8,  b=8, note="HERO — wide shot of the park in morning light"),
  S(11, 9.9,  b=5, note="sitting down on the bench"),
]

TITLES = [                       # (text, act index, seconds into act, dur)
  ("MORNING COFFEE", 0, 0.3, 2.0),
]

def build():
    out = dict(fps_n=FPS_N, fps_d=FPS_D, clips=[], music=[], titles=[], acts=[])
    t = 0.0

    def emit(s, at, elapsed, period=None):
        c = clips[s["idx"]]
        avail = c["dur"] - 0.05 - s["tin"]
        if s["beats"]:
            dur = snap(s["beats"] * period)
        elif period is not None:
            base = snap(s["dur"])
            dur = snap(base + ((-(elapsed + base)) % period))
        else:
            dur = snap(s["dur"])
        while period and dur * s["speed"] > avail and dur > period:
            dur = snap(dur - period)
        if dur * s["speed"] > avail:
            dur = snap(max(0.2, avail / s["speed"]))
            print(f'  ! c{s["idx"]:03d} clamped to {dur:.2f}s (source {c["dur"]:.1f}s)')
        # ⛔ `t` is NOT optional: qc.py builds its overrun check on it, and
        #    without it that check crashes with a KeyError. It is also the only
        #    key that survives a re-pull (clip indices get renumbered).
        rec = dict(src=c["path"], offset=snap(at + elapsed), tin=snap(s["tin"]),
                   dur=dur, speed=s["speed"], audio=s["audio"], t=c["t"],
                   name=f"c{s['idx']:03d} {s['note'][:42]}", note=s["note"], idx=s["idx"])
        # ⛔ beat-snap stretches the PICTURE past the end of the spoken line, and
        #    the tail would then start the next sentence — which repeats from
        #    the start in the following shot. So the AUDIO ends where the
        #    sentence was actually written, even if the picture runs longer.
        if s["audio"] == "nat" and s["dur"] and period and dur > snap(s["dur"]) + 1e-6:
            rec["adur"] = snap(s["dur"])
        if s.get("rot"):                     # camera was mounted upside down
            rec["rot"] = s["rot"]
        for k, v in TRIM.get(c["cam"], {}).items():
            rec[k] = v
        for k in ("ev","con","sat"):                               # scene overrides
            if s.get(k) is not None: rec[k] = s[k]
        if s.get("gain") is not None: rec["gain"] = s["gain"]
        out["clips"].append(rec)
        return snap(elapsed + dur)

    el = 0.0
    for s in COLD:
        el = emit(s, 0.0, el)
    t = el

    for ai, (act, shots) in enumerate(zip(ACTS, [A2])):
        tr = beats[act["track"]]
        period = tr["beat_period"]
        ms = min(tr["beats"], key=lambda b: abs(b - act["mstart"]))
        act_start = t
        el = 0.0
        for s in shots:
            el = emit(s, act_start, el, period)
        out["acts"].append(dict(name=act["name"], start=snap(act_start), dur=el,
                                track=act["track"], bpm=tr["bpm"], period=period))
        out["music"].append(dict(src=str(MUSIC/act["track"]), offset=snap(act_start),
            tin=snap(ms), dur=el, gain=act["gain"],
            fade_in=1.0 if ai == 0 else 0.35,
            fade_out=3.5 if ai == len(ACTS)-1 else 1.6, name=act["track"]))
        t = snap(act_start + el)

    out["total"] = t
    for m in out["music"]:
        m["ducks"] = []
        for c in out["clips"]:
            if c["audio"] == "nat" and c["offset"] >= m["offset"] - 1e-6 \
               and c["offset"] < m["offset"] + m["dur"]:
                m["ducks"].append([round(c["offset"] - m["offset"], 3),
                                   round(c["offset"] - m["offset"] + c["dur"], 3)])
    for txt, ai, off, dur in TITLES:
        base = out["acts"][ai]["start"]
        out["titles"].append(dict(text=txt, offset=snap(base + off), dur=snap(dur)))
    out["titles"].sort(key=lambda t: t["offset"])
    return out

if __name__ == "__main__":
    e = build()
    json.dump(e, open(OUT_JSON, "w"), indent=1, ensure_ascii=False)
    tot = e["total"]
    print(f"\nTOTAL {int(tot//60)}:{tot%60:05.2f}   shots={len(e['clips'])}  "
          f"music={len(e['music'])}  titles={len(e['titles'])}")
    for a in e["acts"]:
        print(f"  {a['name'][:30]:30} start {a['start']:6.2f}  dur {a['dur']:6.2f}  "
              f"{a['bpm']:5.1f}bpm  {a['track'][:40]}")
    nat = [c for c in e['clips'] if c['audio']=='nat']
    print(f"  real-audio shots: {len(nat)}")
    for c in nat: print(f"    {c['offset']:6.2f}s  {c['name'][:56]}")
