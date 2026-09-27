#!/usr/bin/env python3
"""transcribe.py <clips_chrono.json> <transcripts.json> [workdir]

mlx-whisper large-v3 over every clip — finds the moments worth keeping at
real speed. Incremental: already-transcribed files are skipped, so re-run it
as new clips land during a shoot day.

Each clips_chrono entry needs "path" (absolute) — build_clips-style probing
provides it. Uses vlog_config's whisper_python/whisper_model/language.
"""
import json, subprocess, sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vlog_config as C

if os.path.isdir("/opt/homebrew/bin") and "/opt/homebrew/bin" not in os.environ.get("PATH", ""):
    os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ["PATH"]

# mlx_whisper may live only in a dedicated virtualenv. Launched with a plain
# `python3` this can die on `ModuleNotFoundError: No module named 'mlx_whisper'`
# AFTER the caller has already backgrounded it — so it looks like "no speech in
# any of those clips" rather than a crash. Re-exec into the configured
# interpreter instead of relying on the caller to remember.
VENV = C.path("whisper_python")
try:
    import mlx_whisper  # noqa: F401
except ModuleNotFoundError:
    if VENV and VENV.exists() and Path(sys.executable).resolve() != VENV.resolve():
        # flush=True is load-bearing: execv replaces the process image without
        # flushing Python's buffer, so an unflushed print is lost outright.
        print(f"re-exec into {VENV} (mlx_whisper is only there)", flush=True)
        os.execv(str(VENV), [str(VENV), os.path.abspath(__file__), *sys.argv[1:]])
    raise SystemExit("mlx_whisper not importable and whisper_python is unset/missing — "
                     "transcription cannot run")
clips = json.load(open(sys.argv[1]))
OUT = Path(sys.argv[2])
WAV = Path(sys.argv[3]) if len(sys.argv) > 3 else OUT.parent / "wav"
WAV.mkdir(parents=True, exist_ok=True)
WORDS = OUT.parent / "words"
WORDS.mkdir(parents=True, exist_ok=True)

import mlx_whisper
MODEL = C.get("whisper_model")
LANGUAGE = C.get("language") or None

# ⛔ large-v3 hallucinates on silence and does it CONFIDENTLY. Measured on one
# shoot day: 11 of 33 clips returned a stock closing phrase ("Thank you for
# watching." and similar) over pure ambience (bus, wind, playground noise).
# The built-in guard does NOT catch this, because whisper only suppresses a
# segment if no_speech_prob > no_speech_threshold AND avg_logprob <
# logprob_threshold — and these hallucinations had avg_logprob -0.16..-0.25,
# far ABOVE the -1.0 threshold. The AND-gate never trips.
#   Filter on no_speech_prob ALONE instead. Measured distribution:
#     hallucinations: 0.765 0.785 0.794 0.865 0.874 0.906 0.963
#     real speech:    0.002 0.011 0.060 0.580 (a short greeting scored 0.580)
# 0.70 splits the two groups, but NOT cleanly: re-checking the same day also
# pushed two REAL short utterances ("Hi." / a two-word phrase) above the line.
# ⛔ So the segment is NOT dropped outright. It gets marked `suspect` and is
# excluded only from words/, which the machine uses to place a cut inside
# silence — that's where a phantom word is actively harmful. The text stays
# in transcripts.json so speech the model just scored low never disappears.
# Measured threshold (33 clips): real speech 0.002 0.011 0.060 0.580 0.706
# 0.714 | hallucinations 0.765 0.785 0.794 0.865 0.874 0.906 0.963.
# The gap is only 0.714-0.765, i.e. narrow. 0.70 deliberately errs toward
# FLAGGING (costs nothing, text stays) rather than missing a phantom word in
# words/. ⛔ Do not tighten to 0.75 without re-measuring across more days.
# ⛔ Loudness is NOT a discriminator: a hallucinating clip measured mean
# -29.8 dB, a clip with real speech -29.2 dB. A loudness gate would kill speech.
# ⛔ condition_on_previous_text=False does NOT help (verified): each clip is
# transcribed independently, so no prior text exists anyway.
# hallucination_silence_threshold doesn't help either — on one test clip it
# produced worse output ("n n n n").
NO_SPEECH_MAX = 0.70
if not Path(MODEL).exists():
    MODEL = "mlx-community/whisper-large-v3-mlx"

res = json.load(open(OUT)) if OUT.exists() else {}
todo = [c for c in clips if c["dur"] >= 2.0 and
        (c["file"] not in res
         or not (WORDS / f"{Path(c['file']).stem}.json").exists())]
for n, c in enumerate(todo):
    w = WAV / (Path(c["file"]).stem + ".wav")
    if not w.exists():
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", c["path"],
                            "-vn", "-ac", "1", "-ar", "16000", str(w)],
                           capture_output=True, text=True)
        if r.returncode != 0:          # clip with no audio stream
            res[c["file"]] = []
            continue
    # word_timestamps is NOT optional. Segment bounds are sentence-level and
    # approximate, so every in/out derived from them eventually clips a word —
    # a cold open was once chopped mid-word because of this, and it was
    # noticeable in the final cut. Word times also expose the pauses segments hide.
    t = mlx_whisper.transcribe(str(w), path_or_hf_repo=MODEL, language=LANGUAGE,
                               word_timestamps=True)
    def suspect(seg):
        return seg.get("no_speech_prob", 0.0) > NO_SPEECH_MAX
    res[c["file"]] = [dict(start=round(s["start"], 2), end=round(s["end"], 2),
                           text=s["text"].strip(),
                           **({"suspect": True,
                               "no_speech": round(s.get("no_speech_prob", 0.0), 3)}
                              if suspect(s) else {}))
                      for s in t["segments"]]
    drop = sum(1 for s in t["segments"] if suspect(s))
    # words/ stays CLEAN — a phantom word on silence would shift a cut into it
    words = [dict(w=x["word"].strip(), s=round(x["start"], 3), e=round(x["end"], 3))
             for seg in t["segments"] if not suspect(seg)
             for x in seg.get("words", [])]
    # cache per clip — reusable across every revision of the cut, and cuts.py
    # reads exactly this to place a cut in the SILENCE between words
    (WORDS / f"{Path(c['file']).stem}.json").write_text(
        json.dumps(words, ensure_ascii=False))
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"{n+1}/{len(todo)} {c['file']}: "
          f"{' | '.join(x['text'] for x in res[c['file']][:3])[:90]}"
          f"{f'   [{drop} segment(s) marked suspect]' if drop else ''}", flush=True)
print(f"transcripts: {len(res)} clips -> {OUT}")
print(f"word timings: {len(list(WORDS.glob('*.json')))} clips -> {WORDS}")
