"""cammatch.py <workdir> [edl.json] — iterative cross-camera color/exposure matching.

Matches every camera's clips to a reference camera by measuring the SAME
MATERIALS (face, shirt, couch) on identically tone-mapped master frames,
then nudging per-clip "wb"/"ev" trims until they converge (2-4 iterations).
Patch-noise lesson: match same materials across cameras, never abstract
"neutral" patches — auto-WB drift makes single neutrals lie by ±5%.

PER PROJECT you must edit: PATCH (frame-time + patch boxes per camera, read
off 620px-wide master grabs) and the trims dict (clip
indices). DAMP=3.5 because wb gains act on HLG/709 SIGNAL and the view
tone-map amplifies them ~3.5x (measured).
"""
import json, subprocess, math, sys
from pathlib import Path
import numpy as np
from PIL import Image

WORKDIR = sys.argv[1] if len(sys.argv) > 1 else "work2"
PREVIEW = Path(__file__).resolve().with_name("preview.py")
V709=("hwupload,scale_vt=color_matrix=bt709:color_primaries=bt709:"
      "color_transfer=bt709,hwdownload,format=p010le,format=yuv420p")
PATCH={  # frame-time -> {patch: box}
 "insta":  (13.5, dict(face=(330,150,430,230), shirt=(180,260,380,340), couch=(460,170,600,270))),
 "iphone": (5.0,  dict(face=(290,110,400,200), shirt=(150,240,350,330), couch=(460,90,590,180))),
 "fuji":   (10.0, dict(face=(350,60,450,150),  shirt=(200,200,420,320), couch=(60,60,230,150))),
}
def render():
    subprocess.run(["python3",str(PREVIEW),
        "edl_cal.json","cal_master.mp4",WORKDIR,"3840","80M"],
        check=True, capture_output=True, text=True)
def measure(cam):
    t,boxes=PATCH[cam]
    subprocess.run(["ffmpeg","-y","-v","error","-init_hw_device","videotoolbox=vt","-filter_hw_device","vt",
        "-ss",str(t),"-i","cal_master.mp4","-frames:v","1","-vf",f"{V709},scale=620:-2","calf.jpg"],check=True)
    img=Image.open("calf.jpg").convert("RGB")
    out={}
    for name,box in boxes.items():
        a=np.asarray(img.crop(box),dtype=float)
        out[name]=(a[...,0].mean(),a[...,1].mean(),a[...,2].mean())
    return out
def ratios(m):
    rg=np.mean([v[0]/v[1] for v in m.values()])
    bg=np.mean([v[2]/v[1] for v in m.values()])
    lum=np.mean([v[1] for v in m.values()])
    return rg,bg,lum

EDL = sys.argv[2] if len(sys.argv) > 2 else "edl_calibration.json"
e=json.load(open(EDL))
e["titles"]=[]                                   # calibration renders unlabeled
trims={"iphone":dict(idx=1,wb=list(e["clips"][1]["wb"]),ev=e["clips"][1].get("ev",0.0)),
       "fuji":  dict(idx=2,wb=list(e["clips"][2]["wb"]),ev=e["clips"][2].get("ev",0.8))}
DAMP=3.5
for it in range(4):
    for cam,t in trims.items():
        c=e["clips"][t["idx"]]; c["wb"]=[round(t["wb"][0],3),round(t["wb"][1],3)]
        if abs(t["ev"])>0.01: c["ev"]=round(t["ev"],2)
        elif "ev" in c: del c["ev"]
    json.dump(e,open("edl_cal.json","w"))
    subprocess.run(["rm","-f"]+[f"{WORKDIR}/shots/s{t['idx']:03d}.mp4" for t in trims.values()])
    subprocess.run(f"rm -f {WORKDIR}/shots/*_t*.mp4 {WORKDIR}/concat_t.txt",shell=True)
    render()
    ref=ratios(measure("insta"))
    print(f"--- iter {it}  ref insta: R/G {ref[0]:.3f} B/G {ref[1]:.3f} L {ref[2]:.0f}")
    done=True
    for cam,t in trims.items():
        rg,bg,lum=ratios(measure(cam))
        erg,ebg,elum=ref[0]/rg, ref[1]/bg, ref[2]/lum
        print(f"    {cam:7} R/G {rg:.3f} (err {erg:+.1%})  B/G {bg:.3f} ({ebg:+.1%})  L {lum:.0f} (x{elum:.2f})"
              f"   wb={t['wb'][0]:.3f},{t['wb'][1]:.3f} ev={t['ev']:.2f}")
        if abs(erg-1)>0.025 or abs(elum-1)>0.06 or abs(ebg-1)>0.035: done=False
        t["wb"][0]*=erg**(1/DAMP); t["wb"][1]*=ebg**(1/DAMP)
        t["ev"]+=math.log2(elum)*0.9
    if done:
        print("CONVERGED"); break
print("final trims:",{c:(round(t['wb'][0],3),round(t['wb'][1],3),round(t['ev'],2)) for c,t in trims.items()})
