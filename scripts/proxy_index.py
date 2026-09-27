#!/usr/bin/env python3
"""proxy_index.py <media_dir> [--push] — the shot library for one shoot day.

Raw 4K HLG runs ~700 MB per minute, so keeping every original forever fills a
home NAS inside a year. A proxy is **1.5%** of the original (measured: a 509 MB clip ->
7.9 MB), so the deal is: **index + proxies live forever, raw lives on a rolling
window.** That is also how the pros do it — auto-generate a proxy on every card
offload, index sampled frames per scene, keep originals in archive storage.

Builds, beside the ingest dir:
  proxies/<HHMMSS>_<cam>.mp4   960px h264, tone-mapped HLG -> Rec.709 so it looks
                               right in any player (a naive HLG decode looks flat)
  thumbs/<HHMMSS>.jpg          4-frame strip, for eyeballing without opening a file
  shot_index.json              the durable record — every fact + the transcript

⚠️ `desc`, `keep` and `tags` are HUMAN/AGENT judgement and are MERGED, never
clobbered, on re-run. Everything else is re-derived from the media each time.
  desc  — one line of what is actually on screen; fill it off the thumb strip,
          never off the filename
  keep  — true pins the RAW permanently (survives the rolling-window cleanup)
  tags  — free-form, e.g. ["kitchen","hero"]

Incremental and idempotent: re-run after every pull.
"""
import json, os, subprocess, sys, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vlog_config as C

TO709 = ("hwupload,scale_vt=color_matrix=bt709:color_primaries=bt709:"
         "color_transfer=bt709,hwdownload,format=p010le,format=yuv420p")
HDRT = ("arib-std-b67", "smpte2084")
ARCHIVE_HOST = C.get("archive_host")
# ⛔ Keep the archive push root separate from any media-server library folder:
# a media server that watches a parent directory can scan proxies into a
# viewer-facing library, and its ignore rules cannot filter whole
# subdirectories reliably. Give the shot library its own dedicated path on
# the archive host, never a path nested under a watched library folder.
ARCHIVE_ROOT = C.get("archive_root")
PROXY_W = 960
TARGET_LUMA = 105.0  # keeps warm skin tones without blowing highlights on a typical HLG->709 kitchen shoot

D = Path(sys.argv[1]).resolve()
PUSH = "--push" in sys.argv
CH = D / "clips_chrono.json"
if not CH.exists():
    raise SystemExit(f"no clips_chrono.json in {D} — run probe.py first")

clips = json.load(open(CH))
tx = {}
txf = D / "transcripts.json"
if txf.exists():
    tx = json.load(open(txf))

PR = D / "proxies"; PR.mkdir(exist_ok=True)
TH = D / "thumbs";  TH.mkdir(exist_ok=True)
IDX = D / "shot_index.json"

# carry human judgement across re-runs
prev = {}
if IDX.exists():
    for c in json.load(open(IDX)).get("clips", []):
        prev[c["t"]] = c


SILENCE_HALLUCINATIONS = {
    "Ďakujem za pozornosť.", "Ďakujem.",
    "Thank you for watching.", "Thanks for watching.", "you", "Bye.",
}


def speech_of(name):
    """Whisper renders silence as a stock phrase (varies by language/model) — filter it out."""
    segs = tx.get(name) or []
    if isinstance(segs, dict):
        segs = segs.get("segments", [])
    out = []
    for s in segs:
        t = (s.get("text") or "").strip()
        if t and t not in SILENCE_HALLUCINATIONS:
            out.append(t)
    return " ".join(out)[:600]


def measure(px, dur, n=10):
    """Sweep the proxy and return (luma, blown_median, blown_peak, ev_suggest).

    ⚠️ SWEEP, don't spot-check. A first pass sampling only 3 frames reported the
    MAX blown for a whole clip as "20.9% blown" when nine of ten frames were
    under 1%. Clipping on a walking shot is TRANSIENT (the lens swings past the
    sky, spikes for a moment, drops back); on a static shot into a window it is
    CONSTANT. Only the median tells those apart, so both are kept: `blown_median`
    = is it really blown, `blown_peak` = are there moments to avoid when picking
    the in-point.

    ⚠️ ev = log2(target / measured) — a plain ratio, NOT scaled by a 2.2 gamma.
    The gamma reasoning ("encoded = linear^(1/2.2), so multiply by 2.2") is wrong
    here because the HLG->709 view transform is not a simple power curve, and it
    overshoots by more than a stop. Measured on a real macro shot (native luma 58):
        ev 0.00 -> 56.9    ev 0.50 -> 80.5    ev 0.71 -> 93.1
        ev 1.10 -> 122.0   ev 1.55 -> 166.4
    The 2.2 model asked for ev 1.55 to reach 95 and would have blown the whole
    clip; the plain-ratio model predicts 0.71 and lands on 93.1. Re-measure with
    this ladder if the pipeline's colour handling ever changes.
    """
    from PIL import Image, ImageStat
    import statistics, math, tempfile
    lums, blown = [], []
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "f.png")
        for k in range(n):
            t = dur * (k + 0.5) / n
            r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}",
                                "-i", str(px), "-frames:v", "1",
                                "-vf", "scale=240:-2", f], capture_output=True)
            if r.returncode or not os.path.exists(f):
                continue
            g = Image.open(f).convert("L")
            data = list(g.getdata())
            lums.append(ImageStat.Stat(g).mean[0])
            blown.append(100.0 * sum(1 for v in data if v >= 250) / len(data))
    if not lums:
        return None, None, None, None
    L = statistics.median(lums)
    bmed, bpeak = statistics.median(blown), max(blown)
    ev = math.log2(TARGET_LUMA / max(L, 1.0))
    if bmed > 2.0:                      # persistently clipped — never lift it
        ev = min(0.0, ev)
    ev = max(-1.0, min(1.5, round(ev * 2) / 2))
    return round(L, 1), round(bmed, 2), round(bpeak, 2), ev


out = []
for c in clips:
    key = c["t"][-6:] if len(c["t"]) >= 6 else c["t"]
    px = PR / f"{key}_{c['cam']}.mp4"
    th = TH / f"{key}.jpg"

    # ⛔ A SIZE FLOOR CANNOT TELL "FINISHED" FROM "KILLED MID-TRANSCODE". A naive
    # guard of `not px.exists() or size < 2000` lets a proxy whose ffmpeg died
    # part-way (timeout kill, Ctrl-C, laptop sleep) come back >2 KB, and EVERY
    # later re-run then SKIPS it — the truncation becomes permanent. Measured
    # case: a 222 s clip left a 22.3 s proxy (exactly 10%), and re-running
    # proxy_index did not repair it. The proxies are the FOREVER half of the
    # shot library (the raw is a rolling window), so this silently corrupts the
    # archive, and in-points picked past the cut simply fail to render a frame.
    # Duration is the only honest completeness test.
    def _dur(p):
        r = subprocess.run(["ffprobe", "-v", "0", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(p)], capture_output=True, text=True)
        try:
            return float(r.stdout.strip())
        except ValueError:
            return -1.0

    stale = False
    if px.exists() and px.stat().st_size >= 2000 and c.get("dur"):
        dp = _dur(px)
        if dp < 0 or abs(dp - c["dur"]) / c["dur"] > 0.05:
            print(f"  ! {px.name} is {dp:.1f}s but the master is {c['dur']:.1f}s "
                  f"— truncated proxy, rebuilding")
            px.unlink()
            th.unlink(missing_ok=True)      # its thumb strip came from the stub too
            stale = True

    if stale or not px.exists() or px.stat().st_size < 2000:
        vf = []
        if (c.get("transfer") or "") in HDRT:
            vf.append(TO709)
        vf.append(f"scale={PROXY_W}:-2")
        # -hwaccel videotoolbox decodes 4K 10-bit HEVC on the media engine instead
        # of the CPU. Measured on a 15 s Insta360 clip: 41.1 s -> 12.4 s (3.3x), with
        # the output byte-identical in size and mean luma 80.64 vs 80.61. Without
        # it a 174-clip shoot day takes two hours of pure software decode.
        cmd = ["ffmpeg", "-y", "-v", "error", "-hwaccel", "videotoolbox",
               "-init_hw_device", "videotoolbox=vt", "-filter_hw_device", "vt",
               "-i", c["path"], "-vf", ",".join(vf),
               "-c:v", "h264_videotoolbox", "-b:v", "1500k", "-tag:v", "avc1",
               "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(px)]
        subprocess.run(cmd, check=True)
        print(f"proxy {px.name}")

    if not th.exists():
        # strip off the PROXY (already Rec.709 — cheap, and colour-honest)
        n, step = 4, c["dur"] / 5
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(px),
                        "-vf", f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{step:.3f})',"
                               f"scale=320:-2,tile={n}x1", "-frames:v", "1",
                        "-q:v", "5", str(th)], check=True)

    p = prev.get(key, {})
    lum, bmed, bpeak, ev = measure(px, c["dur"])
    out.append(dict(
        t=key, cam=c["cam"], file=c["file"], dur=round(c["dur"], 2),
        fps=c["fps"], w=c["w"], h=c["h"], transfer=c.get("transfer", ""),
        proxy=f"proxies/{px.name}", thumb=f"thumbs/{th.name}",
        luma_native=lum, blown_median=bmed, blown_peak=bpeak, ev_suggest=ev,
        speech=speech_of(c["file"]),
        desc=p.get("desc", ""), keep=p.get("keep"), tags=p.get("tags", []),
    ))

day = D.name.replace("-ingest", "")
raw_bytes = sum(Path(c["path"]).stat().st_size for c in clips if Path(c["path"]).exists())
px_bytes = sum(f.stat().st_size for f in PR.glob("*.mp4"))
doc = dict(date=day, generated=datetime.date.today().isoformat(),
           ingest_dir=str(D), count=len(out),
           raw_mb=round(raw_bytes / 1e6, 1), proxy_mb=round(px_bytes / 1e6, 1),
           described=sum(1 for c in out if c["desc"]),
           keepers=sum(1 for c in out if c.get("keep")),
           clips=out)
json.dump(doc, open(IDX, "w"), indent=1, ensure_ascii=False)
print(f"\n-> {IDX}  ({len(out)} clips)")
print(f"   raw {doc['raw_mb']/1000:.2f} GB -> proxies {doc['proxy_mb']:.0f} MB "
      f"({100*px_bytes/max(raw_bytes,1):.1f}%)")
print(f"   described {doc['described']}/{len(out)}   keepers {doc['keepers']}")
if doc["described"] < len(out):
    print("   NOTE: fill `desc` off the thumb strips — an index without "
          "descriptions cannot answer \"make me a vlog from this month\"")

if PUSH:
    if not ARCHIVE_HOST or not ARCHIVE_ROOT:
        print("PUSH SKIPPED: archive_host/archive_root not set in vlog_config — nothing to push to.")
        raise SystemExit(0)
    dest = f"{ARCHIVE_ROOT}/{day}"
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                        ARCHIVE_HOST, f"mkdir -p {dest}"], capture_output=True, text=True)
    if r.returncode != 0:
        print("PUSH SKIPPED: archive host unreachable. Re-run --push later.")
        raise SystemExit(2)
    for args in (["./proxies/", f"{ARCHIVE_HOST}:{dest}/proxies/"],
                 ["./thumbs/", f"{ARCHIVE_HOST}:{dest}/thumbs/"],
                 ["./shot_index.json", f"{ARCHIVE_HOST}:{dest}/"]):
        r = subprocess.run(["rsync", "-a", "-e", "ssh"] + args, cwd=D,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"PUSH FAILED: {r.stderr.strip()[:300]}")
    import hashlib
    local = hashlib.md5(open(IDX, "rb").read()).hexdigest()
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", ARCHIVE_HOST,
                        f"md5sum {dest}/shot_index.json"], capture_output=True, text=True)
    remote = (r.stdout.split() or [""])[0]
    if local != remote:
        raise SystemExit(f"PUSH UNVERIFIED: md5 {local[:8]} vs {remote[:8]}")
    print(f"-> {ARCHIVE_HOST}:{dest}  (verified md5 {local[:8]})")
