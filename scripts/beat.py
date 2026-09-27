"""beat.py [music_dir] [out.json] [names.json] — onset-envelope beat tracker.

Normalised autocorrelation tempo + comb-filter phase. Outputs tempo and an
explicit beat-time grid for cutting. music_dir defaults to
<vlog_config MUSIC_LIB>/tracks; out.json defaults to <music_dir>/../catalog.json.
Pass names.json (a JSON list of filenames) to scan only those tracks instead of
every file in music_dir.
"""
import json,subprocess,sys,numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vlog_config as C
SR=22050; HOP=256; WIN=1024
def envelope(path, tmax=180):
    r=subprocess.run(["ffmpeg","-v","error","-t",str(tmax),"-i",str(path),
        "-f","f32le","-ac","1","-ar",str(SR),"-"],capture_output=True)
    x=np.frombuffer(r.stdout,dtype=np.float32).astype(np.float64)
    if x.size<SR*3: return None,None,None
    n=(x.size-WIN)//HOP
    fr=np.lib.stride_tricks.as_strided(x,(n,WIN),(x.strides[0]*HOP,x.strides[0]))
    w=np.hanning(WIN)
    S=np.abs(np.fft.rfft(fr*w,axis=1))
    S=np.log1p(S*10)                       # log compression -> perceptual
    e=np.maximum(0,np.diff(S,axis=0)).sum(axis=1)
    e=e-np.convolve(e,np.ones(31)/31,mode="same")   # local-mean subtraction
    e=np.maximum(0,e)
    fps=SR/HOP
    return e,fps,x
def tempo(e,fps,lo=60,hi=180):
    N=len(e); ee=(e-e.mean())/(e.std()+1e-9)
    maxlag=int(fps*60/lo)+2
    ac=np.array([ (ee[:N-k]*ee[k:]).sum()/(N-k) for k in range(1,maxlag) ])
    lags=np.arange(1,maxlag); bpms=60*fps/lags
    ok=(bpms>=lo)&(bpms<=hi)
    sc=ac.copy()
    # harmonic enhancement: reward lags whose 2x also correlates
    for i,k in enumerate(lags):
        if 2*k < maxlag-1: sc[i]+=0.5*ac[2*k-1]
        if 3*k < maxlag-1: sc[i]+=0.25*ac[3*k-1]
    sc[~ok]=-1e9
    k=lags[int(np.argmax(sc))]
    return 60*fps/k, k
def beats(e,fps,k):
    """comb-filter phase: choose offset maximising energy on the grid, then snap
       each predicted beat to the nearest local onset peak."""
    N=len(e)
    best,bo=-1,0
    for off in range(k):
        idx=np.arange(off,N,k)
        s=e[idx].sum()
        if s>best: best,bo=s,off
    grid=np.arange(bo,N,k)
    snapped=[]
    win=max(2,k//8)
    for g in grid:
        a,b=max(0,g-win),min(N,g+win+1)
        if b<=a: continue
        snapped.append(int(a+np.argmax(e[a:b])))
    out=[]
    for s in snapped:
        t=s/fps
        if not out or t-out[-1]>0.15: out.append(round(t,3))
    return out
if __name__=="__main__":
    MU=Path(sys.argv[1]).expanduser() if len(sys.argv)>1 else (C.MUSIC_LIB/"tracks")
    OUT=Path(sys.argv[2]).expanduser() if len(sys.argv)>2 else (MU.parent/"catalog.json")
    names=json.load(open(sys.argv[3])) if len(sys.argv)>3 else None
    res={}
    files=[MU/n for n in names] if names else sorted(MU.iterdir())
    for p in files:
        if not p.exists(): print("MISSING",p.name); continue
        e,fps,x=envelope(p)
        if e is None: continue
        bpm,k=tempo(e,fps)
        bt=beats(e,fps,k)
        dur=float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
            "-of","csv=p=0",str(p)],capture_output=True,text=True).stdout.strip() or 0)
        # energy profile in 8 s buckets over whole track
        b=int(8*SR); prof=[round(float(np.sqrt((x[i:i+b]**2).mean())),3) for i in range(0,x.size,b)]
        res[p.name]={"dur":round(dur,1),"bpm":round(bpm,1),"beat_period":round(k/fps,4),
                     "beats":bt[:400],"prof":prof}
        print(f'{p.name[:50]:50} {dur:6.1f}s {bpm:6.1f}bpm  beats={len(bt)}  prof0-6={prof[:6]}',flush=True)
    json.dump(res,open(OUT,"w"),indent=1)
    print(f"-> {OUT}")
