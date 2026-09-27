#!/usr/bin/env python3
"""Shared configuration for vlog-cut scripts.

Resolution order for every key (last one wins):
  1. built-in defaults below
  2. ~/.config/vlog-cut/config.json   (or the file named by $VLOG_CONFIG)
  3. environment variables  VLOG_<KEY>  (e.g. VLOG_INGEST_ROOT=/Volumes/Media/ingest)

See config.example.json for a documented example. Every path supports `~`.

  python3 vlog_config.py      # print the resolved configuration
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HOME = Path.home()
CONFIG_FILE = Path(os.environ.get("VLOG_CONFIG", HOME / ".config/vlog-cut/config.json")).expanduser()

DEFAULTS: dict[str, str] = {
    # where per-day ingest/project dirs live: <INGEST_ROOT>/<YYYY-MM-DD>-ingest/
    "ingest_root": "~/Movies/vlog-ingest",
    # music library: <MUSIC_LIB>/tracks/*.mp3 + catalog.json (built by music.py scan)
    "music_lib": "~/Music/vlog",
    # small state files (pending.json hand-off between sessions)
    "state_dir": "~/.config/vlog-cut",
    # where finished review copies are placed for the human to watch
    "review_dir": "~/Desktop",
    # optional shell command run with the review file path as its last argument,
    # e.g. a script that sends the file to your phone. Empty = no hook.
    "review_hook": "",
    # Apple Photos library (macOS only, read-only, used by photos_pull.py)
    "photos_library": "~/Pictures/Photos Library.photoslibrary",
    # mount point of a camera card / action camera (card_resume_copy.py)
    "card_volume": "",
    # python interpreter that has mlx_whisper installed (empty = current python)
    "whisper_python": "",
    # mlx-whisper model: a local dir or a Hugging Face repo id
    "whisper_model": "mlx-community/whisper-large-v3-mlx",
    # spoken language code for Whisper ("en", "de", ...); empty = auto-detect
    "language": "",
    # local Ollama vision model for advisory pre-triage (local_triage.py)
    "triage_model": "gemma4:12b",
    # optional archive over SSH: user@host and a root path on that host. Empty = no push.
    "archive_host": "",
    "archive_root": "",
}

_PATH_KEYS = {"ingest_root", "music_lib", "state_dir", "review_dir", "photos_library",
              "card_volume", "whisper_python", "archive_root"}


def _load() -> dict[str, str]:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError as e:
            raise SystemExit(f"invalid JSON in {CONFIG_FILE}: {e}")
        cfg.update({k: str(v) for k, v in data.items() if k in DEFAULTS and not k.startswith("_")})
    for k in DEFAULTS:
        v = os.environ.get("VLOG_" + k.upper())
        if v is not None:
            cfg[k] = v
    return cfg


CFG = _load()


def get(key: str) -> str:
    return CFG[key]


def path(key: str) -> Path | None:
    """Expanded Path for a path key, or None when it is unset."""
    v = CFG[key]
    if key in _PATH_KEYS and v and key != "archive_root":
        return Path(v).expanduser()
    return Path(v) if v else None


INGEST_ROOT = path("ingest_root")
MUSIC_LIB = path("music_lib")
STATE_DIR = path("state_dir")
PENDING = STATE_DIR / "pending.json"
REVIEW_DIR = path("review_dir")


if __name__ == "__main__":
    print(f"config file: {CONFIG_FILE} ({'found' if CONFIG_FILE.exists() else 'not found, defaults'})")
    for k, v in CFG.items():
        print(f"  {k:15} {v}")
