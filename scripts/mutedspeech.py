#!/usr/bin/env python3
"""mutedspeech.py <edl.json> <transcripts.json> [triage_dir] — flag muted shots
that likely contain on-screen speech, and build a triage sheet to eyeball them.

The failure this catches: a muted montage shot framing a face that is visibly
mid-sentence — the viewer sees lips move over music and reads it as a sync
error. Caught this exact case on one cut: a muted mirror shot with a clearly
talking face.

Two gates, ANDed — each alone over-flags about 4×:
  * transcript: a non-hallucination Whisper segment overlaps the shot's
    [tin, tin+dur·speed] window ("Thank you for watching." etc. = Whisper's
    rendering of silence, not speech);
  * loudness: ≥ 0.3 s of speech-band (300–3000 Hz) audio above −33 dBFS RMS
    actually inside that window (Whisper's segment timestamps are coarse —
    a "0–8.4 s" segment whose words really sit at 6–8 s).

No DSP separates a toddler from a fountain reliably at 1.5 s windows (measured:
envelope-CV of real speech 0.42 vs fountain 0.36) — so the output is a SHORTLIST
plus a labelled contact sheet, and the verdict comes from looking:
  * face clearly talking → shift tin into silence, or make the shot "nat"
    (if the line is good, nat is usually the better film);
  * voice is off-camera / wide shot / back of head → leave muted.
"""
import json, subprocess, sys, math
from pathlib import Path
import numpy as np

SR = 16000
GATE_DB, MIN_LOUD = -33.0, 0.3
HALL = {"Thank you for watching.", "Thanks for watching!", "Thank you.",
        "Ďakujem za pozornosť.", "Ďakujem.", "...", "🎵🎵🎵", ""}

def loud_seconds(src, ss, dur):
    r = subprocess.run(["ffmpeg","-v","error","-ss",f"{ss:.3f}","-i",src,
                        "-t",f"{dur:.3f}","-vn","-ac","1","-ar",str(SR),
                        "-af","highpass=f=300,lowpass=f=3000",
                        "-f","f32le","-"], capture_output=True)
    x = np.frombuffer(r.stdout, dtype=np.float32)
    win = SR // 10; n = len(x)//win
    if n == 0: return 0.0
    rms = np.sqrt((x[:n*win].reshape(n, win)**2).mean(axis=1))
    return float((20*np.log10(rms+1e-9) > GATE_DB).sum()) * 0.1

def sheet(flags, outdir):
    from PIL import Image, ImageDraw, ImageFont
    out = Path(outdir); out.mkdir(parents=True, exist_ok=True)
    try: F = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 14)
    except Exception: F = ImageFont.load_default()
    TW, TH, LAB, COLS = 360, 203, 30, 4
    cells = []
    for c, txt in flags:
        mid = c["tin"] + c["dur"]*c.get("speed",1.0)/2
        p = out/f"f{c.get('idx',0):03d}_{c['offset']:.0f}.jpg"
        if not p.exists():
            subprocess.run(["ffmpeg","-y","-v","error","-ss",f"{mid:.2f}","-i",c["src"],
                "-frames:v","1","-vf",f"scale={TW}:-2","-q:v","5",str(p)], capture_output=True)
        t = c["offset"]
        cells.append((p, f"c{c.get('idx',0):03d} @{int(t//60)}:{t%60:04.1f} "
                         f"tin={c['tin']:.1f}+{c['dur']:.1f}", txt[:46]))
    rows = math.ceil(len(cells)/COLS) or 1
    img = Image.new("RGB", (TW*COLS, (TH+LAB)*rows), (15,15,15))
    d = ImageDraw.Draw(img)
    for k, (p, l1, l2) in enumerate(cells):
        x, y = (k % COLS)*TW, (k // COLS)*(TH+LAB)
        try:
            th = __import__("PIL.Image", fromlist=["open"]).open(p).convert("RGB")
            th.thumbnail((TW, TH)); img.paste(th, (x, y+LAB))
        except Exception: pass
        d.text((x+4, y+1),  l1, fill=(255,220,0),  font=F)
        d.text((x+4, y+15), l2, fill=(120,220,255), font=F)
    o = out/"mutedspeech_triage.jpg"
    img.save(o, quality=78)
    print(f"\ntriage sheet: {o}")

def main():
    e = json.load(open(sys.argv[1]))
    tr = json.load(open(sys.argv[2]))
    flags = []
    for c in e["clips"]:
        if c.get("audio") == "nat": continue
        v = tr.get(f"c{c.get('idx',-1):03d}")
        if not v or "segs" not in v: continue
        a, b = c["tin"], c["tin"] + c["dur"]*c.get("speed",1.0)
        hits = [s for s in v["segs"] if s["e"] > a and s["s"] < b
                and s["t"].strip() not in HALL]
        if not hits: continue
        loud = loud_seconds(c["src"], a, b-a)
        if loud < MIN_LOUD: continue
        txt = " | ".join(s["t"].strip() for s in hits)
        t = c["offset"]
        flags.append((c, txt))
        print(f"MUTED+SPEECH {int(t//60)}:{t%60:05.2f}  loud={loud:.1f}s  "
              f"{c['name'][:44]:44}  {txt[:60]}")
    print(f"\n{len(flags)} flagged of "
          f"{sum(1 for c in e['clips'] if c.get('audio')!='nat')} muted shots — "
          f"LOOK before deciding; off-camera voices are fine muted")
    if flags and len(sys.argv) > 3:
        sheet(flags, sys.argv[3])
    return 1 if flags else 0

if __name__ == "__main__":
    sys.exit(main())
