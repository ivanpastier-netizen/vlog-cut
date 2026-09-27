#!/usr/bin/env python3
"""cuts.py <project_dir> <json-list> — turn a phrase into an exact (tin, adur)
using WORD boundaries + surrounding silence.

json-list is a JSON array of [idx, phrase] or [idx, phrase, occurrence] tuples,
e.g. '[[9, "olive oil pours"], [11, "spaghetti", 1]]'.

The v1 bug: cuts placed on whisper's SEGMENT bounds, which are sentence-level and
coarse -> first/last words clipped. Here the cut goes into the gap BETWEEN words:
  tin  = midpoint of the silence before the first word (capped at 0.35 s of lead)
  tout = midpoint of the silence after the last word  (capped at 0.35 s of tail)
so the consonant attack and the final syllable both survive.
"""
import json, sys, unicodedata
from pathlib import Path

# Words are keyed by the clip's FILENAME, not its index — a clip's INDEX moves
# whenever a later wave lands mid-day (probe.py re-sorts chronologically), so an
# index-keyed cache would silently return another clip's words.
LEAD, TAIL = 0.35, 0.40
D = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
_CL = None

def _clips():
    global _CL
    if _CL is None:
        for name in ("clips_trip.json", "clips_chrono.json"):
            f = D / name
            if f.exists():
                _CL = {c["idx"]: c for c in json.loads(f.read_text())}
                break
        else:
            raise SystemExit(f"no clips_trip.json / clips_chrono.json in {D}")
    return _CL

def load(idx):
    c = _clips()[idx]
    f = D / "words" / f"{Path(c['file']).stem}.json"
    if not f.exists():                       # legacy index-keyed cache
        f = D / "words" / f"words_{idx:03d}.json"
    if not f.exists():
        raise SystemExit(f"no word timings for c{idx:03d} ({c['file']}) — "
                         "re-run transcribe.py (it needs word_timestamps=True)")
    return json.loads(f.read_text())

def find(words, phrase, occurrence=0):
    """match on a normalised word sequence; returns (i0, i1) inclusive"""
    # Fold diacritics before comparing. ASR spelling of diacritics is a
    # coin toss ("cafe" vs "café"), so an exact match makes every phrase
    # lookup fail with a bare NOT FOUND and no hint why.
    def norm(x):
        d = unicodedata.normalize("NFKD", x.lower())
        return "".join(ch for ch in d if ch.isalnum() and not unicodedata.combining(ch))
    target = [norm(w) for w in phrase.split() if norm(w)]
    seq = [norm(w["w"]) for w in words]
    hits = []
    for i in range(len(seq) - len(target) + 1):
        if seq[i:i+len(target)] == target:
            hits.append((i, i + len(target) - 1))
    if not hits:
        return None
    return hits[min(occurrence, len(hits) - 1)]

def bounds(idx, phrase, occurrence=0):
    w = load(idx)
    hit = find(w, phrase, occurrence)
    if not hit:
        return None
    i0, i1 = hit
    s, e = w[i0]["s"], w[i1]["e"]
    prev_e = w[i0-1]["e"] if i0 > 0 else max(0.0, s - LEAD)
    next_s = w[i1+1]["s"] if i1 + 1 < len(w) else e + TAIL
    tin  = max(0.0, s - min(LEAD, max(0.06, (s - prev_e) / 2)))
    tout = e + min(TAIL, max(0.08, (next_s - e) / 2))
    return round(tin, 3), round(tout - tin, 3), " ".join(x["w"] for x in w[i0:i1+1])

if __name__ == "__main__":
    if D is None:
        raise SystemExit(__doc__)
    for idx, phrase, *rest in json.loads(sys.argv[2]):
        occ = rest[0] if rest else 0
        r = bounds(idx, phrase, occ)
        if not r:
            print(f"c{idx:03d}  !! NOT FOUND: {phrase!r}")
        else:
            tin, dur, txt = r
            print(f"c{idx:03d}  tin={tin:7.3f}  adur={dur:6.3f}   {txt}")
