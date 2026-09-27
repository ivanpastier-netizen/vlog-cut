"""The vlog music library — index, search, fetch, back up.

The library is a plain folder of audio files on the Mac.

    <music_lib>/tracks/      the audio (mp3/wav/m4a/flac/opus)
    <music_lib>/catalog.json artist, title, duration, BPM, beat grid, energy profile
    <music_lib>/catalog.md   the same, human-readable

`music_lib` is set in the shared config (see vlog_config.py). `backup` mirrors
the tracks folder over SSH to `archive_host`/`archive_root` if those are set
in config; it is a no-op (with a message) otherwise.

Usage
    music.py index [--all] [--jobs N]     index new tracks (--all recomputes everything)
    music.py find [--bpm 90-110] [--dur 150+] [--artist X] [--q text] [--new] [--limit N]
    music.py expand [--dry-run]           everything new from the artists already in here
    music.py fetch <soundcloud-url> ...   add an artist page, playlist or single track
    music.py backup [--full] [--prune]    mirror to archive_host (new files only unless --full)
    music.py stats                        what's in the library

`find --bpm` matches half and double tempo too — the tracker reads plenty of ~89 BPM tracks
as 178, and a double-time grid still contains the half-time one. `--exact-bpm` turns it off.

`expand` and `fetch` are resumable — interrupt them freely, re-run to continue. SoundCloud
rate-limits hard; both back off on HTTP 403 rather than hammering through it.

`find` prints the exact filenames an EDL wants. The beat grid is precomputed here, so an
EDL reads catalog.json directly — no per-project beats.json, no beat.py run per cut.
"""
import argparse, json, os, re, shutil, subprocess, sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from beat import envelope, tempo, beats as beat_grid  # noqa: E402
import vlog_config as C  # noqa: E402

LIB     = C.MUSIC_LIB
TRACKS  = LIB / "tracks"
CATALOG = LIB / "catalog.json"
CAT_MD  = LIB / "catalog.md"
USAGE   = LIB / "usage.json"
# Immutable manual selections survive an EDL being re-cut or overwritten.
HISTORY = LIB / "usage_history.json"
PROJECTS = C.INGEST_ROOT

def _pick_rsync():
    """Prefer a Homebrew rsync (3.x) if present — see cmd_backup for why."""
    for p in ("/opt/homebrew/bin/rsync", "/usr/local/bin/rsync"):
        if Path(p).exists():
            return p
    return shutil.which("rsync") or "rsync"

RSYNC   = _pick_rsync()
AUDIO   = {".mp3", ".wav", ".m4a", ".flac", ".opus", ".aac"}

# Filenames follow yt-dlp's "%(title)s_%(id)s - %(uploader)s.%(ext)s" — keep it that way
# so a track's SoundCloud id stays in the name and dedupe never needs a database.
# Split on the LAST " - ": titles contain dashes ("04 Koudlam - See You All") and so do
# artists ("Ben Murray-Smith"), so anchoring on the separator is the only safe read.
SID_RE = re.compile(r"^(?P<title>.*)_(?P<sid>\d{6,})$")


def parse_name(stem):
    title, sep, artist = stem.rpartition(" - ")
    if not sep:
        title, artist = stem, "?"
    m = SID_RE.match(title)
    sid = m["sid"] if m else None
    return {"title": (m["title"] if m else title).strip(), "artist": artist.strip(), "sid": sid}


def analyse(path):
    """duration + BPM + beat grid + 8 s RMS energy profile for one file."""
    p = Path(path)
    try:
        e, fps, x = envelope(p)
        if e is None:
            return p.name, None
        bpm, k = tempo(e, fps)
        grid = beat_grid(e, fps, k)
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout.strip() or 0)
        import numpy as np
        b = 8 * 22050
        prof = [round(float(np.sqrt((x[i:i + b] ** 2).mean())), 3) for i in range(0, x.size, b)]
        meta = parse_name(p.stem)
        return p.name, {
            **meta,
            "file": p.name,
            "dur": round(dur, 1),
            "bpm": round(bpm, 1),
            "beat_period": round(k / fps, 4),
            "beats": grid[:400],
            "prof": prof,
            "energy": round(sum(prof) / len(prof), 4) if prof else 0.0,
            "added": date.today().isoformat(),
        }
    except Exception as exc:                       # a corrupt file must not kill the run
        return p.name, {"file": p.name, "error": str(exc)[:200], **parse_name(p.stem)}


def load():
    return json.loads(CATALOG.read_text()) if CATALOG.exists() else {}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def scan_usage(save=True):
    """Derive music history from every local EDL plus published catalog records.

    This is deliberately conservative: a track selected in an abandoned EDL still counts
    as used. The library is large enough that avoiding a false repeat matters more than
    rescuing one candidate from the used set.
    """
    used = {}

    def add(track, source):
        if not track:
            return
        track = Path(str(track)).name
        rec = used.setdefault(track, {"uses": 0, "sources": []})
        if source not in rec["sources"]:
            rec["sources"].append(source)
            rec["uses"] += 1

    if PROJECTS.exists():
        for p in sorted(PROJECTS.rglob("edl_*.json")):
            try:
                e = json.loads(p.read_text())
            except Exception:
                continue
            source = str(p.relative_to(PROJECTS))
            names = set()
            for m in e.get("music", []):
                names.add(m.get("name") or m.get("track") or m.get("src"))
            for act in e.get("acts", []):
                names.add(act.get("track"))
            for name in names:
                add(name, source)

        # Older published records sometimes have no surviving EDL but do name the music.
        # Resolve those human-readable names against title+artist in catalog.json.
        records = PROJECTS / "vlog_catalog_records"
        cat = load()
        if records.exists():
            for p in sorted(records.glob("*.json")):
                if p.name.startswith("_"):
                    continue
                try:
                    rec = json.loads(p.read_text())
                    music = _norm(str(rec.get("music") or ""))
                except Exception:
                    continue
                if not music:
                    continue
                for filename, meta in cat.items():
                    title = _norm(meta.get("title", ""))
                    artist = _norm(meta.get("artist", ""))
                    # Exact phrase containment, not raw substring matching: the one-letter
                    # title "U" must not match the published track "You used to".
                    title_hit = len(title) >= 4 and f" {title} " in f" {music} "
                    artist_hit = not artist or f" {artist} " in f" {music} "
                    if title_hit and artist_hit:
                        add(filename, f"vlog_catalog_records/{p.name}")

    # A song auditioned in a real cut may later disappear when that EDL is revised.
    # Keep deliberately marked selections out of the candidate pool permanently.
    if HISTORY.exists():
        try:
            historic = json.loads(HISTORY.read_text()).get("tracks", {})
        except Exception:
            historic = {}
        for name, rec in historic.items():
            note = rec.get("note", "manual selection") if isinstance(rec, dict) else "manual selection"
            add(name, f"usage_history: {note}")

    out = {
        "generated": date.today().isoformat(),
        "policy": "conservative: any local EDL or published catalog mention counts as used",
        "tracks": dict(sorted(used.items())),
    }
    if save:
        USAGE.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    return out


def save(cat):
    CATALOG.write_text(json.dumps(cat, indent=1))
    rows = sorted((v for v in cat.values() if "bpm" in v),
                  key=lambda v: (v["artist"].lower(), -v.get("bpm", 0)))
    out = [f"# Vlog music library — {len(cat)} tracks", "",
           f"`{TRACKS}` · regenerate with `music.py index`", "",
           "| artist | title | len | bpm | energy | file |",
           "|---|---|---|---|---|---|"]
    for v in rows:
        m, s = divmod(int(v["dur"]), 60)
        out.append(f'| {v["artist"]} | {v["title"][:48]} | {m}:{s:02d} | '
                   f'{v["bpm"]:.0f} | {v["energy"]:.3f} | `{v["file"]}` |')
    CAT_MD.write_text("\n".join(out) + "\n")


def cmd_index(a):
    TRACKS.mkdir(parents=True, exist_ok=True)
    cat = {} if a.all else load()
    files = sorted(p for p in TRACKS.iterdir() if p.suffix.lower() in AUDIO)
    todo = [p for p in files if p.name not in cat or "bpm" not in cat.get(p.name, {})]
    gone = [k for k in cat if not (TRACKS / k).exists()]
    for k in gone:
        del cat[k]
    print(f"{len(files)} files · {len(todo)} to analyse · {len(gone)} stale entries dropped")
    if todo:
        with ProcessPoolExecutor(max_workers=a.jobs) as ex:
            for i, (name, rec) in enumerate(ex.map(analyse, [str(p) for p in todo]), 1):
                if rec:
                    cat[name] = rec
                    tag = f'{rec.get("bpm", 0):5.1f}bpm {rec.get("dur", 0):6.1f}s' if "bpm" in rec else "FAILED"
                    print(f"[{i}/{len(todo)}] {tag}  {name[:60]}", flush=True)
                if i % 25 == 0:
                    save(cat)
    save(cat)
    print(f"-> {CATALOG}\n-> {CAT_MD}")


def cmd_find(a):
    cat = load()
    rows = [v for v in cat.values() if "bpm" in v]
    if a.unused:
        used = scan_usage(save=True)["tracks"]
        rows = [v for v in rows if v["file"] not in used]
    if a.bpm:
        lo, _, hi = a.bpm.partition("-")
        lo, hi = float(lo), float(hi or lo)
        def hit(v):
            # Beat trackers land on the wrong octave routinely — dozens of tracks in a real library read ~178
            # when they are ~89. A double-time grid still contains the half-time one (cut on
            # every other beat), so a search for 85-95 must return them. --exact-bpm opts out.
            cands = [v["bpm"]] if a.exact_bpm else [v["bpm"], v["bpm"] / 2, v["bpm"] * 2]
            return any(lo <= c <= hi for c in cands)
        rows = [v for v in rows if hit(v)]
    if a.dur:
        if a.dur.endswith("+"):
            rows = [v for v in rows if v["dur"] >= float(a.dur[:-1])]
        elif a.dur.endswith("-"):
            rows = [v for v in rows if v["dur"] <= float(a.dur[:-1])]
        else:
            lo, _, hi = a.dur.partition("-")
            rows = [v for v in rows if float(lo) <= v["dur"] <= float(hi or lo)]
    if a.artist:
        rows = [v for v in rows if a.artist.lower() in v["artist"].lower()]
    if a.q:
        rows = [v for v in rows if a.q.lower() in v["file"].lower()]
    if a.energy:
        lo, _, hi = a.energy.partition("-")
        rows = [v for v in rows if float(lo) <= v["energy"] <= float(hi or 1)]
    if a.new:
        rows = [v for v in rows if v.get("added", "") >= a.new]
    if a.intro:
        # Opening an act on a track that starts quiet and then lifts buys a free build:
        # the first shots breathe, the drop lands on a cut. `prof` is RMS per 8 s.
        rows = [v for v in rows if lift(v) >= a.intro]
    rows.sort(key=lambda v: -lift(v) if a.sort == "intro"
              else -v["bpm"] if a.sort == "bpm" else -v["energy"])
    for v in rows[:a.limit]:
        m, s = divmod(int(v["dur"]), 60)
        f = fold(v["bpm"])
        oct_ = "" if abs(f - v["bpm"]) < 0.1 else f' (~{f:.0f})'
        extra = f' lift={lift(v):.2f}' if (a.intro or a.sort == "intro") else ""
        print(f'{v["bpm"]:5.1f}bpm{oct_:7} {m:>2}:{s:02d} e={v["energy"]:.3f}{extra}  {v["file"]}')
    print(f"-- {len(rows)} match, showing {min(len(rows), a.limit)}", file=sys.stderr)


def cmd_usage(a):
    usage = scan_usage(save=True)
    rows = sorted(usage["tracks"].items(), key=lambda kv: (-kv[1]["uses"], kv[0].lower()))
    print(f"{len(rows)} used track(s) -> {USAGE}")
    for name, rec in rows:
        print(f"{rec['uses']:3}  {name}")
        if getattr(a, "sources", False):
            for source in rec["sources"]:
                print(f"       {source}")


def cmd_mark_used(a):
    """Persist a selected draft track so a later EDL rewrite cannot resurrect it."""
    try:
        hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    except Exception:
        hist = {}
    tracks = hist.setdefault("tracks", {})
    tracks[Path(a.track).name] = {"marked": date.today().isoformat(), "note": a.note}
    HISTORY.write_text(json.dumps(hist, indent=1, ensure_ascii=False) + "\n")
    cmd_usage(a)


def lift(v):
    """How much louder the body is than the first 16 s. 0 = flat, 1 = silent intro."""
    p = v.get("prof") or []
    if len(p) < 6:
        return 0.0
    intro = sum(p[:2]) / 2
    body = sum(p[2:8]) / len(p[2:8])
    return 0.0 if body <= 0 else max(0.0, 1 - intro / body)


def fold(bpm):
    """The same tempo expressed in the 70–140 range people actually count in."""
    while bpm > 140:
        bpm /= 2
    while bpm < 70:
        bpm *= 2
    return bpm


def cmd_fetch(a):
    """Add a new artist (or a single track). Resolve ids first, then hand them to the same
    throttled downloader `expand` uses — otherwise a big artist page walks straight into 403."""
    TRACKS.mkdir(parents=True, exist_ok=True)
    have = on_disk_ids()
    ids = []
    for url in a.urls:
        rows = _sc(["--flat-playlist", "--print", "%(id)s\t%(title)s", url], 300)
        found = [i.strip() for i, _, _t in (l.partition("\t") for l in rows.splitlines())
                 if i.strip().isdigit()]
        new = [i for i in found[:a.max] if i not in have and i not in ids]
        print(f"== {url}  online={len(found)} new={len(new)}", flush=True)
        ids += new
    if not ids:
        print("nothing new")
        return
    _download(ids, a.workers)
    cmd_index(argparse.Namespace(all=False, jobs=a.jobs))


def _sc(args, timeout):
    try:
        return subprocess.run(["yt-dlp", "--no-update", "--socket-timeout", "20", *args],
                              capture_output=True, text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return ""


def cmd_expand(a):
    """Pull everything NEW from the artists already in the library.

    An artist's SoundCloud handle changes over time (an old handle can 404
    today), so resolve the live profile from a track id we already own rather
    than guessing the URL.
    """
    cat = load()
    have = {v["sid"] for v in cat.values() if v.get("sid")}
    cache = LIB / ".discover.json"

    if a.from_cache and cache.exists():
        disc = json.loads(cache.read_text())
    else:
        by_artist = {}
        for v in cat.values():
            if v.get("sid"):
                by_artist.setdefault(v["artist"], []).append(v["sid"])
        disc = {}
        for artist, sids in sorted(by_artist.items(), key=lambda kv: -len(kv[1]))[:a.artists]:
            url = ""
            for sid in sids[:3]:              # a deleted track shouldn't lose us the artist
                out = _sc(["--print", "%(uploader_url)s",
                           f"https://api.soundcloud.com/tracks/{sid}"], 90)
                url = next((l.strip() for l in out.splitlines()
                            if l.strip().startswith("https://soundcloud.com/")), "")
                if url:
                    break
            if not url:
                print(f"{artist:28} UNRESOLVED ({len(sids)} owned)", flush=True)
                continue
            rows = _sc(["--flat-playlist", "--print", "%(id)s\t%(title)s", f"{url}/tracks"], 300)
            tracks = [{"id": i.strip(), "title": t.strip()}
                      for i, _, t in (l.partition("\t") for l in rows.splitlines())
                      if i.strip().isdigit()]
            new = [t for t in tracks if t["id"] not in have]
            disc[artist] = {"url": url, "total": len(tracks), "owned": len(sids), "new": new}
            print(f"{artist:28} online={len(tracks):4} owned={len(sids):3} NEW={len(new):4}",
                  flush=True)
        cache.write_text(json.dumps(disc, indent=1))

    todo, seen = [], set()
    for artist, d in disc.items():
        # two catalog artists can resolve to one profile (an artist can be filed under
        # a shortened alias too), so dedupe ids across artists, not just against what's
        # on disk
        fresh = [t["id"] for t in d["new"] if t["id"] not in have and t["id"] not in seen]
        seen.update(fresh[:a.per_artist])
        todo += fresh[:a.per_artist]
    print(f"== {len(todo)} tracks to fetch from {len(disc)} artists")
    if a.dry_run or not todo:
        return

    TRACKS.mkdir(parents=True, exist_ok=True)
    _download(todo, a.workers)
    cmd_index(argparse.Namespace(all=False, jobs=a.jobs))


def on_disk_ids():
    """Which SoundCloud ids we already hold, read from the filenames themselves.
    Cheaper and more honest than the catalog — a file lands before it is indexed."""
    return {sid for p in TRACKS.iterdir() if p.suffix.lower() in AUDIO
            for sid in [parse_name(p.stem)["sid"]] if sid}


def _round(ids, workers, pause):
    from concurrent.futures import ThreadPoolExecutor
    logdir = LIB / ".logs"
    logdir.mkdir(exist_ok=True)
    for old in logdir.glob("fetch-*.log"):
        old.unlink()          # a previous run with more workers leaves logs that read as
                              # live failures long after they stopped mattering
    slices = [ids[i::workers] for i in range(workers)]      # round-robin: spread the artists

    def worker(n, chunk):
        if not chunk:
            return
        with open(logdir / f"fetch-{n}.log", "w") as fh:
            subprocess.run([
                "yt-dlp", "--no-update", "-x", "--audio-format", "mp3", "--audio-quality", "0",
                "--download-archive", str(LIB / f".archive-{n}"), "--no-overwrites",
                "--ignore-errors", "--no-warnings", "--quiet", "--no-progress",
                "--concurrent-fragments", "4", "--socket-timeout", "30", "--retries", "5",
                "--retry-sleep", "exp=5:120", "--sleep-requests", str(pause),
                "--print", "after_move:%(id)s",
                "-o", str(TRACKS / "%(title)s_%(id)s - %(uploader)s.%(ext)s"),
                *[f"https://api.soundcloud.com/tracks/{t}" for t in chunk]],
                stdout=fh, stderr=subprocess.STDOUT)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(lambda t: worker(*t), enumerate(slices)))


def _download(ids, workers=3, rounds=12):
    """SoundCloud rate-limits hard: six parallel workers earn a wall of HTTP 403 within a
    couple of minutes. So keep concurrency low, sleep between requests, and treat a stalled
    round as a temporary block — back off and come back, don't diagnose.

    Every round re-derives what is still missing from the files on disk, so this is safe to
    interrupt and re-run: nothing is downloaded twice.
    """
    import time
    remaining = [i for i in ids if i not in on_disk_ids()]
    backoff = 60

    def clear(probes):
        """Is the block over? A cheap metadata read, so a blocked round is never started.
        Try a few ids — a single one could be deleted or private and look like a block."""
        for wait in (0, 60, 120, 240, 480, 900, 900, 900):
            if wait:
                print(f"-- blocked (403), waiting {wait}s", flush=True)
                time.sleep(wait)
            for probe in probes[:3]:
                out = subprocess.run(["yt-dlp", "--no-update", "--socket-timeout", "20",
                                      "--print", "%(id)s",
                                      f"https://api.soundcloud.com/tracks/{probe}"],
                                     capture_output=True, text=True)
                if out.returncode == 0 and probe in out.stdout:
                    return True
        return False

    for r in range(1, rounds + 1):
        if not remaining:
            break
        if not clear(remaining):
            print("-- still blocked after backoff, stopping; re-run to resume", flush=True)
            break
        print(f"-- round {r}: {len(remaining)} left, {workers} workers", flush=True)
        _round(remaining, workers, pause=1.5)
        still = [i for i in remaining if i not in on_disk_ids()]
        got = len(remaining) - len(still)
        remaining = still
        print(f"-- round {r}: +{got} (remaining {len(remaining)})", flush=True)
        if remaining and got < max(5, len(remaining) // 50):
            print(f"-- throttled, sleeping {backoff}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 900)
        else:
            backoff = 60

    merged = LIB / ".yt-dlp-archive"                       # one archive again, for next time
    seen = set(merged.read_text().splitlines()) if merged.exists() else set()
    for f in LIB.glob(".archive-*"):
        seen.update(f.read_text().splitlines())
        f.unlink()
    merged.write_text("\n".join(sorted(x for x in seen if x)) + "\n")
    print(f"== {len(ids) - len(remaining)}/{len(ids)} fetched, {len(remaining)} unresolved")


def cmd_backup(a):
    """Mirror the tracks folder to a remote host over SSH (archive_host/archive_root in
    config). This is optional: with no archive_host configured, it's a clean no-op.

    Two traps this avoids on macOS: `openrsync` (protocol 29) deadlocks against an rsync
    3.x server, so prefer a Homebrew rsync binary if one is installed; and going over an
    SMB mount instead of SSH makes a few thousand small files crawl.
    """
    host = C.get("archive_host")
    root = C.get("archive_root")
    if not host or not root:
        print("archive_host/archive_root not set in config — skipping backup")
        return
    if not Path(RSYNC).exists() and RSYNC == "rsync" and not shutil.which("rsync"):
        sys.exit("rsync not found — `brew install rsync` (macOS openrsync can deadlock "
                 "against some remote rsync servers)")
    # --exclude='*' both stops non-audio transferring AND protects it from --delete,
    # which lets other content sharing that remote directory survive a prune.
    filters = ["--include=*.mp3", "--include=*.wav", "--include=*.m4a", "--include=*.flac",
               "--include=*.opus", "--include=*.aac", "--exclude=*"]
    dest = f"{host}:{root}"
    cmd = [RSYNC, "-rt", "--no-perms", "--info=stats1", "-e", "ssh",
           *(["--delete"] if a.prune else []), *(["--ignore-times"] if a.full else []),
           *filters, f"{TRACKS}/", f"{dest}/"]
    print(" ".join(cmd), flush=True)
    if subprocess.run(cmd).returncode:
        sys.exit("rsync failed")
    meta = [str(f) for f in (CATALOG, CAT_MD, LIB / "README.md") if f.exists()]
    if meta:
        subprocess.run([RSYNC, "-rt", "--no-perms", "-e", "ssh", *meta, f"{dest}/"])
    print(f"mirrored to {dest}")


def cmd_stats(a):
    cat = load()
    ok = [v for v in cat.values() if "bpm" in v]
    by = {}
    for v in ok:
        by[v["artist"]] = by.get(v["artist"], 0) + 1
    tot = sum(v["dur"] for v in ok)
    print(f"{len(cat)} tracks · {len(ok)} indexed · {tot/3600:.1f} h · {len(by)} artists")
    print(f"bpm {min(v['bpm'] for v in ok):.0f}–{max(v['bpm'] for v in ok):.0f}")
    for k, c in sorted(by.items(), key=lambda kv: -kv[1])[:20]:
        print(f"  {c:4}  {k}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index"); p.set_defaults(fn=cmd_index)
    p.add_argument("--all", action="store_true"); p.add_argument("--jobs", type=int, default=6)

    p = sub.add_parser("find"); p.set_defaults(fn=cmd_find)
    p.add_argument("--bpm"); p.add_argument("--dur"); p.add_argument("--artist")
    p.add_argument("--q"); p.add_argument("--energy"); p.add_argument("--new")
    p.add_argument("--sort", default="bpm", choices=["bpm", "energy", "intro"])
    p.add_argument("--exact-bpm", action="store_true", help="don't match half/double tempo")
    p.add_argument("--intro", type=float, nargs="?", const=0.45, default=None,
                   metavar="MIN", help="only tracks that open quiet and lift (0-1, default .45)")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--unused", action="store_true",
                   help="exclude every track in an EDL, vlog record, or manual usage history")

    p = sub.add_parser("usage"); p.set_defaults(fn=cmd_usage)
    p.add_argument("--sources", action="store_true", help="also print projects per track")

    p = sub.add_parser("mark-used"); p.set_defaults(fn=cmd_mark_used)
    p.add_argument("track", help="exact music filename to reserve")
    p.add_argument("--note", default="manual selection", help="why this track stays reserved")

    p = sub.add_parser("fetch"); p.set_defaults(fn=cmd_fetch)
    p.add_argument("urls", nargs="+", help="artist page (…/tracks), playlist, or single track")
    p.add_argument("--max", type=int, default=400, help="cap per URL")
    p.add_argument("--workers", type=int, default=3); p.add_argument("--jobs", type=int, default=6)

    p = sub.add_parser("expand"); p.set_defaults(fn=cmd_expand)
    p.add_argument("--artists", type=int, default=25, help="how many library artists to walk")
    p.add_argument("--per-artist", type=int, default=250)
    p.add_argument("--from-cache", action="store_true", help="reuse the last discovery pass")
    p.add_argument("--workers", type=int, default=3,
                   help="parallel yt-dlp processes — above 3 SoundCloud starts returning 403")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--jobs", type=int, default=6)

    p = sub.add_parser("backup"); p.set_defaults(fn=cmd_backup)
    p.add_argument("--full", action="store_true")
    p.add_argument("--prune", action="store_true", help="also delete tracks the library dropped")

    p = sub.add_parser("stats"); p.set_defaults(fn=cmd_stats)

    a = ap.parse_args()
    a.fn(a)
