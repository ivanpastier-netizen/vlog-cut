#!/usr/bin/env python3
"""Read-only local state inspector for vlog-cut projects.

The output is intentionally small and deterministic so a cheaper model can decide what to
do next without reconstructing project state from prose or filenames.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vlog_config as C

HOME = Path.home()
PENDING = C.PENDING
REQUIRED_COMMANDS = ("python3", "ffmpeg", "ffprobe")
OPTIONAL_COMMANDS = ("exiftool", "xmllint", "ssh", "md5")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def item_count(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("clips", "items", "shots", "segments"):
            if isinstance(value.get(key), list):
                return len(value[key])
        return len(value)
    return None


def description_stats(value: Any) -> dict[str, int] | None:
    if isinstance(value, dict):
        for key in ("clips", "items", "shots"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
        else:
            value = list(value.values())
    if not isinstance(value, list):
        return None
    records = [x for x in value if isinstance(x, dict)]
    if not records:
        return {"total": 0, "described": 0, "missing": 0}
    described = sum(bool(str(x.get("desc", "")).strip()) for x in records)
    return {"total": len(records), "described": described, "missing": len(records) - described}


def local_triage_stats(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("clips"), dict):
        return None
    records = [record for record in value["clips"].values() if isinstance(record, dict)]
    statuses = {status: sum(record.get("status") == status for record in records)
                for status in ("ok", "partial", "failed")}
    return {
        "advisory_only": value.get("advisory_only") is True,
        "model": value.get("model"),
        "total": len(records),
        **statuses,
    }


def newest(paths: list[Path]) -> str | None:
    existing = [p for p in paths if p.exists()]
    if not existing:
        return None
    return str(max(existing, key=lambda p: p.stat().st_mtime))


def is_render(path: Path) -> bool:
    """Exclude camera/source media while retaining authored outputs."""
    lower = path.name.lower()
    source_prefixes = ("insta_", "iphone_", "fuji_", "vid_", "img_", "dscf")
    if lower.startswith(source_prefixes) or "_clean." in lower:
        return False
    markers = ("review", "wip", "master", "720p", "1080p", "4k", "share")
    return path.suffix.lower() in {".mp4", ".m4v"} and (
        any(marker in lower for marker in markers) or path.suffix.lower() == ".m4v"
    )


def inspect_project(project: Path) -> dict[str, Any]:
    project = project.expanduser().resolve()
    clips_path = project / "clips_chrono.json"
    trip_path = project / "clips_trip.json"
    clips_file = trip_path if trip_path.exists() else clips_path
    transcripts_path = project / "transcripts.json"
    shot_index_path = project / "shot_index.json"
    local_triage_path = project / "local_triage.json"

    clips = load_json(clips_file) if clips_file.exists() else None
    transcripts = load_json(transcripts_path) if transcripts_path.exists() else None
    shot_index = load_json(shot_index_path) if shot_index_path.exists() else None
    local_triage = load_json(local_triage_path) if local_triage_path.exists() else None

    edl_py = sorted(project.glob("edl_*.py"))
    edl_json = sorted(project.glob("edl*.json"))
    renders = sorted(
        p for pattern in ("*.mp4", "*.m4v") for p in project.glob(pattern) if is_render(p)
    )
    proxy_dirs = [project / "proxies", project / "thumbs", project / "sheets"]
    proxy_count = len(list((project / "proxies").glob("*"))) if (project / "proxies").is_dir() else 0

    state: dict[str, Any] = {
        "path": str(project),
        "exists": project.is_dir(),
        "clips_file": str(clips_file) if clips_file.exists() else None,
        "clips": item_count(clips),
        "transcripts": item_count(transcripts),
        "shot_index": description_stats(shot_index),
        "local_triage": local_triage_stats(local_triage),
        "proxy_count": proxy_count,
        "analysis_dirs": {p.name: p.is_dir() for p in proxy_dirs},
        "edl_python": [str(p) for p in edl_py],
        "edl_json": [str(p) for p in edl_json],
        "renders": [str(p) for p in renders],
        "newest_render": newest(renders),
        "workdirs": {name: (project / name).is_dir() for name in ("work", "work4k")},
    }

    if not project.is_dir():
        state["stage"] = "missing-project"
        state["next"] = "Provide an existing project/ingest directory."
    elif not clips_file.exists():
        state["stage"] = "ingest"
        state["next"] = "Run Photos/card ingest and probe.py."
    elif not transcripts_path.exists() or not shot_index_path.exists() or proxy_count == 0:
        state["stage"] = "analysis"
        state["next"] = "Run transcription, proxy_index.py, and proxy contact sheets."
    elif state["shot_index"] and state["shot_index"]["missing"]:
        state["stage"] = "describe"
        if isinstance(state["clips"], int) and state["clips"] > 20 and state["local_triage"] is None:
            state["next"] = (
                "Run optional local_triage.py, then have the paid editorial model verify "
                "labelled frames and fill missing shot descriptions."
            )
        else:
            state["next"] = "Inspect labelled proxy sheets and fill missing shot descriptions."
    elif not edl_py or not edl_json:
        state["stage"] = "edit"
        state["next"] = "Create/rebuild the authored EDL from the template."
    elif not renders:
        state["stage"] = "render-review"
        state["next"] = "Render the 720p review in work/, then run qc.py."
    else:
        state["stage"] = "review-or-qc"
        state["next"] = "Verify the newest render, run/read QC, and check pending review state."
    return state


def inspect_pending() -> dict[str, Any] | None:
    if not PENDING.exists():
        return None
    data = load_json(PENDING)
    if not isinstance(data, dict):
        return {"path": str(PENDING), "valid": False}
    return {
        "path": str(PENDING),
        "valid": True,
        "project": data.get("project"),
        "status": data.get("status"),
        "review_sent": data.get("review_sent"),
        "distributed": data.get("distributed", False),
        "edl": data.get("edl"),
        "workdir": data.get("workdir"),
    }


def inspect_local() -> dict[str, Any]:
    commands = {
        cmd: {"available": shutil.which(cmd) is not None, "path": shutil.which(cmd)}
        for cmd in REQUIRED_COMMANDS + OPTIONAL_COMMANDS
    }
    music_lib = C.MUSIC_LIB
    whisper_python = C.path("whisper_python")
    photos_library = C.path("photos_library")
    review_hook = C.get("review_hook")
    paths = {
        "music_catalog": bool(music_lib) and (music_lib / "catalog.json").exists(),
        "whisper_python": bool(whisper_python) and whisper_python.exists(),
        "photos_library": bool(photos_library) and photos_library.exists(),
        "review_hook_configured": bool(review_hook),
    }
    return {
        "commands": commands,
        "paths": paths,
        "required_ready": all(commands[c]["available"] for c in REQUIRED_COMMANDS),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect local vlog-cut readiness and project state")
    parser.add_argument("project", nargs="?", help="ingest/project directory")
    parser.add_argument("--compact", action="store_true", help="print one-line JSON")
    args = parser.parse_args()

    pending = inspect_pending()
    result: dict[str, Any] = {"local": inspect_local(), "pending": pending}
    if args.project:
        state = inspect_project(Path(args.project))
        if pending and pending.get("valid"):
            project_root = str(Path(state["path"]))
            pending_paths = (pending.get("edl"), pending.get("workdir"))
            same_project = any(
                isinstance(p, str) and p.startswith(project_root + "/") for p in pending_paths
            )
            if same_project and pending.get("status") == "distributed":
                state["stage"] = "distributed"
                state["next"] = "Audit catalog/source bundle or start the next project."
            elif same_project and pending.get("status") == "awaiting-review":
                state["stage"] = "awaiting-review"
                state["next"] = "Wait for the owner's review feedback; do not publish yet."
        result["project"] = state
    print(json.dumps(result, ensure_ascii=False, indent=None if args.compact else 2, sort_keys=True))
    return 0 if result["local"]["required_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
