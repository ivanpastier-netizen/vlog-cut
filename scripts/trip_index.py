#!/usr/bin/env python3
"""trip_index.py <out-dir> <day-ingest-dir> [<day-ingest-dir> ...] — merge days into one trip.

A vacation is not a shoot DAY. Every per-day artifact (clips_chrono, transcripts,
shot_index) is scoped to one dir, and proxy_index keys clips on HHMMSS alone —
which collides across days. So a multi-day film needs a trip-level index that
keys every shot on the full YYYYMMDD_HHMMSS, which never moves no matter how many
later waves land.

  trip_index.py ~/Movies/vlog-ingest/_trip-beach \
                ~/Movies/vlog-ingest/2026-01-0{1,2}-ingest

Writes into <out-dir>:
  clips_trip.json    every clip, globally re-indexed, chronological across days
  transcripts.json   merged, keyed by filename (unique across days)
  words/             merged word-timing caches (symlinks — they are read-only)
  shot_index.json    merged durable record incl. desc/keep/tags
  trip.json          the manifest: which days, counts, minutes, cameras

⛔ An EDL must resolve shots through K("YYYYMMDD_HHMMSS"), never a bare index —
re-running this after a new day lands renumbers everything after it.
"""
import json, os, sys
from pathlib import Path

if len(sys.argv) < 3:
    raise SystemExit(__doc__)
OUT = Path(sys.argv[1]).resolve(); OUT.mkdir(parents=True, exist_ok=True)
DAYS = [Path(p).resolve() for p in sys.argv[2:]]

clips, tx, shots, days = [], {}, [], []
W = OUT / "words"; W.mkdir(exist_ok=True)
for D in DAYS:
    ch = D / "clips_chrono.json"
    if not ch.exists():
        print(f"skip {D.name}: no clips_chrono.json"); continue
    day = json.loads(ch.read_text())
    for c in day:
        c["day"] = D.name.replace("-ingest", "")
        clips.append(c)
    t = D / "transcripts.json"
    if t.exists():
        tx.update(json.loads(t.read_text()))
    si = D / "shot_index.json"
    if si.exists():
        doc = json.loads(si.read_text())
        for s in doc.get("clips", []):
            s["day"] = D.name.replace("-ingest", "")
            # HHMMSS collides across days; carry the full stamp
            s["t_full"] = f"{s['day'].replace('-','')}_{s['t']}"
            s["proxy_abs"] = str(D / s["proxy"])
            shots.append(s)
    wd = D / "words"
    if wd.is_dir():
        for f in wd.glob("*.json"):
            link = W / f.name
            if not link.exists():
                try: link.symlink_to(f)
                except OSError: link.write_bytes(f.read_bytes())
    days.append(dict(dir=str(D), day=D.name.replace("-ingest", ""), clips=len(day),
                     minutes=round(sum(c["dur"] for c in day) / 60, 1)))

clips.sort(key=lambda c: c["t"])
for i, c in enumerate(clips):
    c["idx"] = i                       # global, chronological across the whole trip
(OUT / "clips_trip.json").write_text(json.dumps(clips, indent=1, ensure_ascii=False))
(OUT / "transcripts.json").write_text(json.dumps(tx, indent=1, ensure_ascii=False))

shots.sort(key=lambda s: s["t_full"])
by_cam = {}
for c in clips:
    k = (c["cam"], c.get("bits"))
    by_cam[f"{k[0]}-{k[1]}bit"] = by_cam.get(f"{k[0]}-{k[1]}bit", 0) + 1
doc = dict(trip=OUT.name, days=days, count=len(clips),
           minutes=round(sum(c["dur"] for c in clips) / 60, 1),
           by_cam=by_cam,
           described=sum(1 for s in shots if s.get("desc")),
           keepers=sum(1 for s in shots if s.get("keep")),
           clips=shots)
(OUT / "shot_index.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False))
(OUT / "trip.json").write_text(json.dumps(dict(days=days, by_cam=by_cam,
    count=len(clips), minutes=doc["minutes"]), indent=1, ensure_ascii=False))

print(f"{OUT}")
for d in days:
    print(f"  {d['day']}: {d['clips']:4d} clips  {d['minutes']:6.1f} min")
print(f"  TOTAL {len(clips)} clips, {doc['minutes']} min   {by_cam}")
print(f"  described {doc['described']}/{len(shots)}   keepers {doc['keepers']}")
print(f"  transcripts {len(tx)}   words {len(list(W.glob('*.json')))}")
