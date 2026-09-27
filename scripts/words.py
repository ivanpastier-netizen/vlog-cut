#!/usr/bin/env python3
"""words.py <project_dir> <json-list-of-idx> — word-level whisper transcript,
the fix for clipped words.

Segment bounds are sentence-level and coarse, so any in/out point derived from
them lands inside a word. word_timestamps=True gives per-word start/end, so a
cut can be placed in the SILENCE between words instead.

Writes words_<idx>.json per clip: [{w, s, e}, ...]
"""
import json, subprocess, sys, tempfile, os
from pathlib import Path
import mlx_whisper

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vlog_config as C

D = Path(sys.argv[1]).resolve()
OUT = D / "words"
OUT.mkdir(exist_ok=True)
clips = {o["idx"]: o for o in json.load(open(D / "clips_chrono.json"))}
MODEL = C.get("whisper_model")
LANGUAGE = C.get("language") or None   # empty string -> auto-detect

for idx in json.loads(sys.argv[2]):
    dst = OUT / f"words_{idx:03d}.json"
    if dst.exists():
        print(f"c{idx:03d} cached"); continue
    c = clips[idx]
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = f.name
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", c["path"],
                    "-ac", "1", "-ar", "16000", wav], check=True)
    r = mlx_whisper.transcribe(wav, path_or_hf_repo=MODEL, language=LANGUAGE,
                               word_timestamps=True)
    os.unlink(wav)
    words = []
    for seg in r["segments"]:
        for w in seg.get("words", []):
            words.append({"w": w["word"].strip(), "s": round(w["start"], 3),
                          "e": round(w["end"], 3)})
    json.dump(words, open(dst, "w"), ensure_ascii=False, indent=0)
    print(f"c{idx:03d} {len(words)} words -> {dst.name}")
    sys.stdout.flush()
