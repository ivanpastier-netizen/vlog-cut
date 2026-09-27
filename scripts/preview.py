#!/usr/bin/env python3
"""preview.py <edl.json> <out.mp4> <workdir> — 720p watchable render of any EDL
(video + real-audio moments normalised + ducked music + verbatim-styled titles)."""
import json,subprocess,sys,os,math,re
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
edl=Path(sys.argv[1]); OUT=Path(sys.argv[2])
# resolve(): concat.txt holds the shot paths and ffmpeg reads them RELATIVE TO THE
# LIST FILE, so a relative workdir yields 'wd/wd/shots/s000.mp4' and the concat dies
# after every shot has already rendered.
WD=Path(sys.argv[3]).resolve(); WD.mkdir(parents=True,exist_ok=True)
e=json.load(open(edl)); FPS="30000/1001"
# optional 4th/5th args: width and video bitrate (default = 720p review copy)
W=int(sys.argv[4]) if len(sys.argv)>4 else 1280
H=int(round(W*9/16/2))*2
VB=sys.argv[5] if len(sys.argv)>5 else "6M"
SH=WD/"shots"; SH.mkdir(exist_ok=True); NA=WD/"nat"; NA.mkdir(exist_ok=True); MB=WD/"mus"; MB.mkdir(exist_ok=True)
def run(c,label="",retries=0):
    # The final 4K mux dies intermittently under videotoolbox with rc!=0 and an
    # EMPTY stderr (seen more than once on real cuts); an identical rerun
    # succeeds. So retry once before giving up.
    for attempt in range(retries+1):
        r=subprocess.run(c,capture_output=True,text=True)
        if r.returncode==0: return
        print(f"ERR {label} (attempt {attempt+1}/{retries+1})",r.stderr[-500:] or "(no stderr)")
    raise SystemExit(1)
# 1. shots
# Modern phones and action cameras shoot Dolby Vision 8.4 (= HLG/BT.2020 base layer), so
# the timeline has a COLOR MODE, resolved once per render:
#   hlg — when most sources are HDR (or the EDL says "color": "hlg"): the master is
#         HEVC Main-10 BT.2100 HLG (+hvc1 or Apple things won't play it); HLG
#         sources pass through untouched, SDR/PQ stragglers are mapped INTO HLG.
#   sdr — legacy projects: H.264 BT.709 as always; HDR stragglers are mapped DOWN
#         to BT.709, or they ship desaturated / washed.
# Either direction of mapping runs in videotoolbox hardware via scale_vt (this
# ffmpeg has no zscale, its `colorspace`/`tonemap` can't read arib-std-b67, and
# `hwdownload` refuses nv12 — download as p010le). Judge color on a rendered
# frame, never on a raw decode: an HLG frame decoded naively always looks flat.
_tcache={}
def _transfer(src):
    if src not in _tcache:
        r=subprocess.run(["ffprobe","-v","error","-select_streams","v:0",
            "-show_entries","stream=color_transfer","-of","csv=p=0",src],
            capture_output=True,text=True)
        # csv=p=0 appends a trailing comma when the stream carries side data
        # (Dolby Vision clips do) — without rstrip the HLG test NEVER matches.
        _tcache[src]=r.stdout.strip().rstrip(",")
    return _tcache[src]
HDRT=("arib-std-b67","smpte2084")
_srcs={c["src"] for c in e["clips"]}
MODE=e.get("color") or ("hlg" if sum(_transfer(s) in HDRT for s in _srcs)>len(_srcs)/2 else "sdr")
HLG=(MODE=="hlg")
print(f"color mode: {MODE}",flush=True)
if HLG:
    _sdr=[s_ for s_ in _srcs if _transfer(s_) not in HDRT]
    if _sdr:
        print(f"note: {len(_sdr)} SDR source(s) in an HLG render — mapped into HLG and",
              flush=True)
        print("    lifted by `ev` (EVSDR/EV8BIT). MEASURE THE RENDER before you act on",
              flush=True)
        print("    this line; it cannot tell you whether the lift landed.", flush=True)
        print("    How: pull one frame of an SDR shot and one of an HLG NEIGHBOUR out of",
              flush=True)
        print("    the finished render through the SAME view map, and compare means —",
              flush=True)
        print("      ffmpeg -init_hw_device videotoolbox=vt -filter_hw_device vt -ss <t>",
              flush=True)
        print("        -i <render> -frames:v 1 -vf hwupload,scale_vt=color_matrix=bt709:",
              flush=True)
        print("        color_primaries=bt709:color_transfer=bt709,hwdownload,format=p010le,",
              flush=True)
        print("        format=yuv420p out.png", flush=True)
        print("    Measured outcomes so far, same code path:", flush=True)
        print("      one shoot, 10-bit Rec.709 + EVSDR 0.50 -> 131.8 / 131.2 in a render",
              flush=True)
        print("        whose HLG neighbours sat at 118.7 / 135.8 / 140.5. It LANDED.",
              flush=True)
        print("      An earlier reading (140.6 native -> 97.4 mapped, `ev` unable to close",
              flush=True)
        print("        it) was measured on a DIFFERENT source and must not be generalised —",
              flush=True)
        print("        acting on it would have dropped a whole HDR day for two 1.8 s shots.",
              flush=True)
        print("    Only if YOUR measurement shows a gap: raise ev, or set color:\"sdr\".",
              flush=True)
        for s_ in sorted(_sdr)[:20]:
            print(f"      SDR: {Path(s_).name}", flush=True)
VC=(["-c:v","hevc_videotoolbox","-profile:v","main10","-tag:v","hvc1",
     "-colorspace","bt2020nc","-color_primaries","bt2020","-color_trc","arib-std-b67",
     "-pix_fmt","p010le"] if HLG else
    ["-c:v","h264_videotoolbox","-pix_fmt","yuv420p",
     "-colorspace","bt709","-color_primaries","bt709","-color_trc","bt709"])
TO709=("hwupload,scale_vt=color_matrix=bt709:color_primaries=bt709:"
       "color_transfer=bt709,hwdownload,format=p010le,format=yuv420p")
# ⛔ `format=yuv420p10le` FIRST is load-bearing. Handed an 8-bit source (an
# action camera writes yuvj420p when 10-bit HLG is off) this chain made
# hevc_videotoolbox fail with `-22 Invalid argument` and write a **0-byte
# segment** — and because preview.py skips any existing s%03d.mp4 over 2000
# bytes, the retry loop and QC both slide past it. Measured: 14 of 174 clips
# would have vanished on one project.
TOHLG=("format=yuv420p10le,hwupload,scale_vt=color_matrix=bt2020nc:"
       "color_primaries=bt2020:color_transfer=arib-std-b67,hwdownload,format=p010le")
for i,c in enumerate(e["clips"]):
    o=SH/f"s{i:03d}.mp4"
    if o.exists() and o.stat().st_size>2000: continue
    sp=c["speed"]; vf=[]
    # A GoPro clipped on upside down records an inverted clip and NOTHING flags it:
    # the shot lands in the cut with the sky at the bottom, and on a beach shot the
    # inversion reads as a puddle reflection, so it survives a contact-sheet review.
    # `rot` on an EDL clip fixes it in the render; originals are never modified.
    if c.get("rot"):
        r=int(c["rot"])%360
        if   r==180: vf.append("hflip,vflip")
        elif r==90:  vf.append("transpose=1")
        elif r==270: vf.append("transpose=2")
        elif r:      sys.exit(f"rot must be 90/180/270, got {c['rot']}")
    # Portrait phone clips can become intentional wide inserts, without black bars.
    if c.get("crop_landscape"):
        vf.append("crop=iw:iw*9/16:(iw-ow)/2:(ih-oh)/2")
    vf+=[f"scale={W}:{H}:force_original_aspect_ratio=decrease",
         f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black"]
    if abs(sp-1)>1e-6: vf.insert(0,f"setpts=PTS/{sp}")
    if HLG and _transfer(c["src"])!="arib-std-b67": vf.insert(0,TOHLG)
    elif not HLG and _transfer(c["src"]) in HDRT: vf.insert(0,TO709)
    # per-clip inter-camera matching trims, applied AFTER the color-mode
    # conversion so every camera is corrected in the same space. Small,
    # signal-domain approximations — meant for <10% nudges, not grading:
    #   "wb": [rr, bb] — channel gains to neutralize a warmer/cooler camera
    #   "ev": stops    — exposure trim for a camera that metered differently
    # ⛔ Run every trim in FLOAT RGB. `colorlevels` renders PURE BLACK on 10-bit
    # planar input (measured: gbrp10le and yuv420p10le both give mean luma 0.0;
    # gbrpf32le 74.9, gbrp 74.3, no-con reference 76.8). `exposure` happened to
    # mask it by forcing a float conversion, so the bug only surfaced on a shot
    # carrying `con` with NO `ev` — ev=0.0 is falsy, the filter is skipped, and
    # two outdoor shots shipped as black frames. Nothing upstream warns; only
    # `checkshots.py` NEAR BLACK caught it. Float also keeps the
    # 10-bit precision that `format=gbrp` would throw away.
    if any(c.get(k) is not None for k in ("wb", "ev", "sat", "con")):
        vf.append("format=gbrpf32le")
    if c.get("wb"): vf.append(f"colorchannelmixer=rr={c['wb'][0]:.3f}:bb={c['wb'][1]:.3f}")
    if c.get("ev"): vf.append(f"exposure=exposure={c['ev']:.2f}")
    #   "con"/"sat" — tone-shape matching: iPhone's computational look is FLAT
    #   (compressed highlights, ~0.7x contrast) and oversaturated; Fuji runs
    #   ~1.5x the Insta's saturation. ⛔ NOT via eq: on these 10-bit HLG
    #   segments eq forces an 8-bit range-mangled conversion and sat=0.75
    #   measurably RAISED saturation. RGB-domain equivalents behave:
    #   saturation = colorchannelmixer mix toward Rec.709 luma,
    #   contrast   = colorlevels linear map around pivot 0.36 (HLG mids).
    if c.get("sat") is not None:
        s=c["sat"]; L=(0.2126,0.7152,0.0722)
        mx=":".join(f"{a}{b}={(s if a==b else 0)+(1-s)*L['rgb'.index(b)]:.4f}"
                    for a in "rgb" for b in "rgb")
        vf.append(f"colorchannelmixer={mx}")
    if c.get("con") is not None:
        cc=c["con"]; p=c.get("pivot",0.36)
        if cc>=1.0:
            imin=p*(cc-1)/cc; imax=imin+1/cc
            lv=f"rimin={imin:.4f}:gimin={imin:.4f}:bimin={imin:.4f}:rimax={imax:.4f}:gimax={imax:.4f}:bimax={imax:.4f}"
        else:
            omin=p*(1-cc); omax=cc+omin
            lv=f"romin={omin:.4f}:gomin={omin:.4f}:bomin={omin:.4f}:romax={omax:.4f}:gomax={omax:.4f}:bomax={omax:.4f}"
        vf.append(f"colorlevels={lv}")
    vf.append(f"fps={FPS}")
    if HLG: vf.append("format=p010le")
    # -frames:v, NOT -t: cutting segments by time lets the fps filter's boundary
    # rounding drop the last frame on ~1 in 4 shots. Individually that is 33 ms
    # (under any per-shot tolerance); across 104 concatenated shots it compounded
    # to 767 ms of video deficit, so the audio mix — placed at EDL offsets — ran
    # visibly behind the picture by the end of the film (audible in the last
    # ~7 s of one cut). Exact frame counts pin the video timeline to the EDL
    # grid; a full-timeline duration check now lives in QC.
    nfr=max(1,round(c["dur"]*30000/1001))
    run(["ffmpeg","-y","-v","error","-init_hw_device","videotoolbox=vt",
         "-filter_hw_device","vt","-hwaccel","videotoolbox","-ss",f"{c['tin']:.4f}",
         "-i",c["src"],"-vf",",".join(vf),"-an"]+VC+["-b:v",VB,
         "-frames:v",str(nfr),str(o)],o.name)
    if i%20==0: print(f"  shot {i}/{len(e['clips'])}",flush=True)
lst=WD/"concat.txt"; lst.write_text("".join(f"file '{SH}/s{i:03d}.mp4'\n" for i in range(len(e['clips']))))
run(["ffmpeg","-y","-v","error","-f","concat","-safe","0","-i",str(lst),"-c","copy",str(WD/"v.mp4")],"concat")
total=e["total"]
# 2. real-audio moments, normalised to sit above the ducked music
items=[]
for i,c in enumerate(e["clips"]):
    if c.get("audio")!="nat": continue
    raw=NA/f"raw{i:03d}.wav"; o=NA/f"n{i:03d}.wav"
    if not raw.exists():
        # ⛔ `adur` — the audio may be SHORTER than the picture. A `d=` shot gets
        # stretched to the next beat so the grid stays aligned, and on a narration
        # shot that stretch reaches past the end of the sentence and picks up the
        # first word of the next one — heard clearly at least twice on real cuts,
        # cutting a sentence off mid-word and repeating a fragment of the next.
        # So the EDL records the sentence length as `adur` and the picture keeps
        # the beat-aligned `dur`.
        adur=c.get("adur") or c["dur"]
        run(["ffmpeg","-y","-v","error","-ss",f"{c['tin']:.4f}","-i",c["src"],"-t",f"{adur:.4f}",
             "-vn","-ac","2","-ar","48000","-af","highpass=f=80",str(raw)],raw.name)
    # ⛔ A reviewer once described the narration audio as clicky and buzzing —
    # that was distortion, and the cause was this step: the gain was derived
    # from MEAN volume and applied with no regard for the PEAK. Speech has a
    # quiet mean and loud peaks, so it got the biggest lifts:
    # the oil-pour clip peaked at -1.1 dBFS and was handed +16.6 dB, then
    # `alimiter` crushed everything above 0 — audible as buzzing/clicking. The
    # old `dynaudnorm=p=0.9:m=6:s=10` made it worse by pumping room tone and
    # breath up between words.
    #
    # Now: EBU R128 two-pass loudnorm. It targets PERCEIVED loudness, respects a
    # true-peak ceiling, and does not pump. Two passes because single-pass
    # loudnorm is a live estimator and drifts on short clips.
    if not o.exists():
        meas=subprocess.run(["ffmpeg","-v","info","-i",str(raw),"-af",
            "loudnorm=I=-17:TP=-1.5:LRA=11:print_format=json","-f","null","-"],
            capture_output=True,text=True).stderr
        m2=None
        try:
            m2=json.loads(meas[meas.rindex("{"):meas.rindex("}")+1])
        except Exception:
            pass
        if m2:
            af=("loudnorm=I=-17:TP=-1.5:LRA=11:"
                f"measured_I={m2['input_i']}:measured_TP={m2['input_tp']}:"
                f"measured_LRA={m2['input_lra']}:measured_thresh={m2['input_thresh']}:"
                f"offset={m2.get('target_offset',0)}:linear=true:print_format=summary")
        else:                      # fall back to single pass rather than to the old bug
            af="loudnorm=I=-17:TP=-1.5:LRA=11"
        run(["ffmpeg","-y","-v","error","-i",str(raw),"-af",af,"-ar","48000",str(o)],o.name)
    items.append((c["offset"],o))
# 3. music with duck automation
for k,m in enumerate(e["music"]):
    o=MB/f"m{k}.wav"
    g=m["gain"]; lin=10**(g/20.); dlin=10**((g-15)/20.)
    duck="+".join(f"between(t,{a:.3f},{b:.3f})" for a,b in m.get("ducks",[])) or "0"
    af=(f"volume='if({duck},{dlin:.5f},{lin:.5f})':eval=frame,"
        f"afade=t=in:st=0:d={m['fade_in']:.2f},"
        f"afade=t=out:st={max(0,m['dur']-m['fade_out']):.2f}:d={m['fade_out']:.2f}")
    if not o.exists():
        # ⛔ A music entry LONGER than its source silently yields a short stem, and
        # the mix then runs dry for the remainder with no warning — a 20 min
        # archive cut against a 3:51 bed lost 17 minutes of audio. `-t` cannot
        # invent material; `-stream_loop -1` before the input can.
        _len=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
                             "-of","csv=p=0",m["src"]],capture_output=True,text=True).stdout.strip()
        try: _avail=float(_len)-m["tin"]
        except Exception: _avail=None
        _loop=["-stream_loop","-1"] if (_avail is not None and m["dur"]>_avail+0.05) else []
        if _loop:
            print(f"  music '{m['name'][:34]}' loops "
                  f"({m['dur']:.0f}s needed, {_avail:.0f}s available)",flush=True)
        run(["ffmpeg","-y","-v","error"]+_loop+["-ss",f"{m['tin']:.4f}","-i",m["src"],"-t",f"{m['dur']:.4f}",
             "-vn","-ac","2","-ar","48000","-af",af,str(o)],o.name)
    items.append((m["offset"],o))
ins=[];fc=[];lab=[]
for k,(off,p) in enumerate(items):
    ins+=["-i",str(p)]; ms=int(round(off*1000))
    fc.append(f"[{k}:a]adelay={ms}|{ms},apad=whole_dur={total:.3f},atrim=0:{total:.3f}[a{k}]")
    lab.append(f"[a{k}]")
fc.append("".join(lab)+f"amix=inputs={len(lab)}:normalize=0:dropout_transition=0[mix]")
# 0.80 ceiling (-1.9 dBFS), not 0.85: AAC encoding overshoots limited peaks by
# ~1 dB, and close-mic'd nat lines pushed a 0.85-limited mix to -0.5 dB max.
fc.append("[mix]alimiter=level_in=1:level_out=0.80:limit=0.80:attack=5:release=60[aout]")
run(["ffmpeg","-y","-v","error"]+ins+["-filter_complex",";".join(fc),"-map","[aout]",
    "-ac","2","-ar","48000",str(WD/"a.wav")],"mix")
# 4. titles (text taken from the EDL's raw title xml) + mux
import re
# Helvetica Neue silently drops accented characters (e.g. a caron,
# Š -> S), so prefer a face that actually has them; the title card is the one
# bit of the preview the viewer reads as text.
def _pickfont():
    for f in ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
              "/System/Library/Fonts/HelveticaNeue.ttc",
              "/System/Library/Fonts/Helvetica.ttc"]:
        if not os.path.exists(f): continue
        try:
            if ImageFont.truetype(f,40).getbbox("Š")[2]>0: return f
        except Exception: pass
    return None
fp=_pickfont()
tp=[]
titles=[]
for t in e.get("raw_titles",[]):
    txts=re.findall(r'<text-style ref="[^"]+">([^<]*)</text-style>',t["xml"])
    titles.append(dict(text="\n".join(x for x in txts if x.strip()),offset=t["offset"],dur=t["dur"]))
titles+= [dict(text=t["text"],offset=t["offset"],dur=t["dur"]) for t in e.get("titles",[])]
for j,t in enumerate(titles):
    p=WD/f"t{j}.png"; img=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(img)
    lines=[l for l in t["text"].split("\n") if l.strip()]
    y=int(H*0.60)
    for n,L in enumerate(lines):
        # a lone act card is the headline, not a subtitle — size it like one.
        # With a second line (the date), keep the headline a headline and drop the
        # date to a subtitle: the old fixed 58/30 shrank a 140 px title to 58 the
        # moment a date was added, which read as a caption rather than a card.
        # Proportional to H so it survives the 720p review / 4K master ladder.
        size=(int(H*0.13) if len(lines)==1
              else (int(H*0.10) if n==0 else int(H*0.042)))
        f=ImageFont.truetype(fp,size,index=0) if fp else ImageFont.load_default()
        bb=d.textbbox((0,0),L,font=f); x=(W-(bb[2]-bb[0]))//2
        # In HLG, PNG 255 lands at PEAK signal (~1000 nits) — a glaring card.
        # ~75% signal is HLG diffuse white, which is what a title should be.
        tw=(191,191,191,238) if HLG else (255,255,255,238)
        # ⛔ That 191 ceiling means the card CANNOT out-brighten a bright scene, so
        # it needs an OUTLINE, not a drop shadow. A 3 px offset shadow vanished
        # against a bright wall and made the card unreadable — the date line was
        # nearly invisible. A stroke scaled to the glyph reads on white walls and
        # on night footage
        # alike, without touching the white level.
        sw=max(3,size//18)
        d.text((x+sw,y+sw),L,font=f,fill=(0,0,0,110))          # soft depth
        d.text((x,y),L,font=f,fill=tw,
               stroke_width=sw,stroke_fill=(0,0,0,205))         # the legibility
        y+=size+8
    img.save(p); tp.append(p)
# ffmpeg 8's threaded transcode scheduler races on framesync (the looped-PNG
# overlay inputs): a whole-timeline overlay graph drops 1-3 interior frames per
# title at NON-DETERMINISTIC positions and appends one extra frame past the
# grid (measured on ffmpeg 8.1: 6/1987 frames gone on one render, 10/5944 on
# another). PTS stay intact so there is no A/V drift — each loss is a
# 33 ms freeze — invisible to synccheck/av_visual_check; only the container
# frame count vs the EDL catches it. -filter_complex_threads 1 did NOT fix it.
#
# So: never run the overlay over the whole film. Burn each title into ONLY the
# segments it touches (tiny graphs, exact -frames:v, verified + retried), then
# re-concat and STREAM-COPY the video into the mux — a copy cannot drop frames,
# and the untouched segments skip a second lossy encode entirely.
def vframes(p):
    r=subprocess.run(["ffprobe","-v","error","-count_packets","-select_streams","v:0",
        "-show_entries","stream=nb_read_packets","-of","csv=p=0",str(p)],
        capture_output=True,text=True)
    return int(re.sub(r"[^0-9]","",r.stdout) or 0)
segfiles=[SH/f"s{i:03d}.mp4" for i in range(len(e["clips"]))]
for j,(p,t) in enumerate(zip(tp,titles)):
    a,b=t["offset"],t["offset"]+t["dur"]; FD=min(0.35,t["dur"]/3)
    for i,c in enumerate(e["clips"]):
        off=c["offset"]; sdur=c["dur"]
        if off+sdur<=a-0.05 or off>=b+0.05: continue
        src=segfiles[i]; o=SH/f"s{i:03d}_t{j}.mp4"
        nfr=max(1,round(sdur*30000/1001))
        # fade times shift into segment-local time. ffmpeg 8 rejects a negative
        # fade st ("Result too large"), so a fade is emitted only if it touches
        # this segment, clamped to st>=0 — a ramp that straddles a cut restarts
        # at the cut, which is invisible at FD<=0.35 s.
        sin_,sot=a-off,b-FD-off
        chain="format=rgba"
        if sin_>-FD: chain+=f",fade=t=in:st={max(0,sin_):.3f}:d={FD:.3f}:alpha=1"
        if sot<sdur: chain+=f",fade=t=out:st={max(0,sot):.3f}:d={FD:.3f}:alpha=1"
        fc=(f"[1:v]{chain}[t];"
            f"[0:v][t]overlay=0:0:enable='between(t,{max(0,a-off-0.05):.3f},{b-off+0.05:.3f})'[v]")
        for attempt in range(3):
            run(["ffmpeg","-y","-v","error","-filter_complex_threads","1","-i",str(src),
                 "-loop","1","-t",f"{sdur+0.5:.3f}","-framerate",FPS,"-i",str(p),
                 "-filter_complex",fc,"-map","[v]","-an"]+VC+["-b:v",VB,
                 "-frames:v",str(nfr),str(o)],o.name)
            if vframes(o)==nfr: break
        else:
            print(f"ERR title seg {o.name}: frame count never exact"); raise SystemExit(1)
        segfiles[i]=o          # later titles overlaying the same segment stack up
# A closing fade to black. Applied to the LAST segment only — a whole-timeline
# fade would force a re-encode of the entire film (at 3840/80M that is minutes of
# GPU for 1.5 s of picture), and the segment is already its own file. The audio
# side is the music's own fade_out, which the act already carries.
FO=float(e.get("fade_out") or 0)
if FO>0 and segfiles:
    last=segfiles[-1]
    ldur=vframes(last)*1001/30000
    d=min(FO,max(0.2,ldur-0.05))
    o=WD/f"{last.stem}_fo.mp4"
    nfr=vframes(last)
    run(["ffmpeg","-y","-v","error","-i",str(last),
         "-vf",f"fade=t=out:st={max(0,ldur-d):.3f}:d={d:.3f}",
         *VC,"-b:v",VB,"-frames:v",str(nfr),str(o)],o.name)
    if vframes(o)==nfr:
        segfiles[-1]=o
        print(f"  fade out {d:.2f}s on the last shot",flush=True)
    else:
        print(f"  WARN fade segment frame count {vframes(o)}!={nfr} — fade skipped",flush=True)
nfr_total=round(e["total"]*30000/1001)
if any(f.name.startswith("s") and "_t" in f.name for f in segfiles):
    lst2=WD/"concat_t.txt"; lst2.write_text("".join(f"file '{f}'\n" for f in segfiles))
    run(["ffmpeg","-y","-v","error","-f","concat","-safe","0","-i",str(lst2),
         "-c","copy",str(WD/"vt.mp4")],"concat_t")
    vfinal=WD/"vt.mp4"
else:
    vfinal=WD/"v.mp4"
if vframes(vfinal)!=nfr_total:
    print(f"ERR {vfinal.name}: {vframes(vfinal)} frames != EDL {nfr_total}"); raise SystemExit(1)
# ⛔ NORMALISE THE MIX TO -18 dB. Without it, the perceived loudness of a day's
# cut depends entirely on how loud the chosen music track happens to be:
# measured on finished masters — one day -23.0 dB, another -17.7 dB, another
# -18.6 dB. A 5.3 dB gap is clearly audible switching between cuts, and qc.py
# alone will not catch it (it only checks the mix stays in a wide band).
_vd = subprocess.run(["ffmpeg","-nostdin","-i",str(WD/"a.wav"),"-af","volumedetect",
                      "-f","null","-"], capture_output=True, text=True).stderr
_m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", _vd)
_gain = 0.0
if _m:
    _gain = max(-6.0, min(12.0, -18.0 - float(_m.group(1))))
    print(f"  loudness: mix {float(_m.group(1)):.1f} dB -> gain {_gain:+.1f} dB (target -18)", flush=True)
# ⛔ Plain gain CLIPS. One cut sat at -23.0 dB with a max of -1.9 dB, so a +5 dB
# lift pushed peaks to 0.0 dB (measured). Always follow the gain with a limiter.
_af = (["-af", f"volume={_gain:.2f}dB,alimiter=limit=-1.0dB:level=disabled"]
       if abs(_gain) > 0.2 else [])
run(["ffmpeg","-y","-v","error","-i",str(vfinal),"-i",str(WD/"a.wav"),
     "-map","0:v","-map","1:a","-c:v","copy"]+_af+["-c:a","aac","-b:a","256k",
     "-movflags","+faststart",str(OUT)],"mux",retries=1)
if vframes(OUT)!=nfr_total:
    print(f"ERR {OUT}: {vframes(OUT)} frames != EDL {nfr_total}"); raise SystemExit(1)
print(f"PREVIEW: {OUT}  ({nfr_total} frames, grid-exact)",flush=True)
