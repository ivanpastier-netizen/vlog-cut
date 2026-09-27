#!/usr/bin/env python3
"""Resumable, advisory local vision pre-triage for vlog proxy clips.

This deliberately writes local_triage.json instead of shot_index.json. Local model output
may prioritize later paid-model inspection, but it never excludes footage or makes an
editorial decision.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vlog_config as C

PROMPT_VERSION = "v1-objective-three-frame"
DEFAULT_MODEL = C.get("triage_model")
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
SAMPLE_FRACTIONS = (0.2, 0.5, 0.8)
ALLOWED_SETTINGS = {"indoor", "outdoor", "vehicle", "unknown"}
ALLOWED_AGES = {"baby", "child", "adult"}
ALLOWED_QUALITY = {"blur", "obstruction", "backlight", "low_light", "none"}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "setting": {"type": "string", "enum": sorted(ALLOWED_SETTINGS)},
        "people_count": {"type": ["integer", "null"], "minimum": 0, "maximum": 20},
        "age_groups": {"type": "array", "items": {"type": "string", "enum": sorted(ALLOWED_AGES)}},
        "visible_actions": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "face_visible": {"type": ["boolean", "null"]},
        "framing_issues": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "quality_issues": {"type": "array", "items": {"type": "string", "enum": sorted(ALLOWED_QUALITY)}},
        "uncertain": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    },
    "required": [
        "setting", "people_count", "age_groups", "visible_actions", "face_visible",
        "framing_issues", "quality_issues", "uncertain",
    ],
}

PROMPT = """Inspect only this single video frame. Return JSON matching the schema.
Report literal visible facts only. Do not identify or name anyone; do not infer family
relationships, emotion, intent, dialogue, story importance, or whether the shot should be
kept. If unclear, use null, unknown, or uncertain. Keep action and issue phrases short.
Do not mention anything that is not visibly supported by this frame."""


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return fallback


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=str(path.parent), prefix=path.name + ".", delete=False) as f:
        json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
        tmp = Path(f.name)
    tmp.replace(path)


def strip_to_json(text: str) -> dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    decoder = json.JSONDecoder()
    for pos, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[pos:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return validate_observation(value)
    raise ValueError("model response contains no valid JSON object")


def short_strings(value: Any, allowed: Optional[Set[str]] = None) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("expected a list")
    result = []
    for item in value[:4]:
        if not isinstance(item, str):
            raise ValueError("list item is not a string")
        item = " ".join(item.strip().split())[:100]
        if item and (allowed is None or item in allowed):
            result.append(item)
    return sorted(set(result))


def validate_observation(value: dict[str, Any]) -> dict[str, Any]:
    setting = value.get("setting")
    if setting not in ALLOWED_SETTINGS:
        raise ValueError("invalid setting")
    people = value.get("people_count")
    if people is not None and (isinstance(people, bool) or not isinstance(people, int) or not 0 <= people <= 20):
        raise ValueError("invalid people_count")
    face = value.get("face_visible")
    if face is not None and not isinstance(face, bool):
        raise ValueError("invalid face_visible")
    return {
        "setting": setting,
        "people_count": people,
        "age_groups": short_strings(value.get("age_groups"), ALLOWED_AGES),
        "visible_actions": short_strings(value.get("visible_actions")),
        "face_visible": face,
        "framing_issues": short_strings(value.get("framing_issues")),
        "quality_issues": short_strings(value.get("quality_issues"), ALLOWED_QUALITY),
        "uncertain": short_strings(value.get("uncertain")),
    }


def ollama_observe(image_path: Path, model: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": PROMPT,
        "images": [base64.b64encode(image_path.read_bytes()).decode("ascii")],
        "format": SCHEMA,
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": 300},
        "keep_alive": "5m",
    }
    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read())
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama unavailable at {OLLAMA_URL}: {exc}") from exc
    if body.get("error"):
        raise RuntimeError(str(body["error"]))
    return strip_to_json(str(body.get("response", "")))


def check_ollama_model(model: str) -> None:
    request = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Ollama is not ready at http://127.0.0.1:11434: {exc}") from exc
    names = {
        str(item.get("name")) for item in body.get("models", [])
        if isinstance(item, dict) and item.get("name")
    }
    if model not in names:
        raise RuntimeError(f"Ollama model {model!r} is not installed; available: {', '.join(sorted(names)) or 'none'}")


def repeated_scalar(values: list[Any]) -> Any:
    present = [v for v in values if v is not None and v != "unknown"]
    if not present:
        return None
    value, count = Counter(present).most_common(1)[0]
    return value if count >= 2 else None


def repeated_items(observations: list[dict[str, Any]], key: str) -> list[str]:
    counts: Counter[str] = Counter()
    for observation in observations:
        counts.update(set(observation[key]))
    return sorted(item for item, count in counts.items() if count >= 2 and item != "none")


def consensus(observations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "setting": repeated_scalar([o["setting"] for o in observations]),
        "people_count": repeated_scalar([o["people_count"] for o in observations]),
        "age_groups": repeated_items(observations, "age_groups"),
        "face_visible": repeated_scalar([o["face_visible"] for o in observations]),
        "quality_issues": repeated_items(observations, "quality_issues"),
        "agreement_frames": len(observations),
    }


def proxy_fingerprint(proxy: Path, model: str) -> dict[str, Any]:
    stat = proxy.stat()
    return {
        "proxy": str(proxy), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "model": model, "prompt_version": PROMPT_VERSION,
    }


def extract_frame(proxy: Path, duration: float, fraction: float, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    second = max(0.0, min(duration * fraction, max(duration - 0.05, 0.0)))
    command = [
        "ffmpeg", "-y", "-v", "error", "-ss", f"{second:.3f}", "-i", str(proxy),
        "-frames:v", "1", "-q:v", "2", str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode or not target.exists():
        raise RuntimeError(result.stderr.strip() or "ffmpeg did not produce a frame")


def clips_from_index(project: Path) -> list[dict[str, Any]]:
    index = load_json(project / "shot_index.json", {})
    clips = index.get("clips") if isinstance(index, dict) else None
    if not isinstance(clips, list):
        raise RuntimeError(f"No clips in {project / 'shot_index.json'}; run proxy_index.py first")
    return [clip for clip in clips if isinstance(clip, dict)]


def self_test() -> None:
    sample = {
        "setting": "outdoor", "people_count": 2, "age_groups": ["adult", "child"],
        "visible_actions": ["walking"], "face_visible": True,
        "framing_issues": [], "quality_issues": ["none"], "uncertain": [],
    }
    parsed = strip_to_json("<think>ignored</think>\n" + json.dumps(sample))
    assert parsed["setting"] == "outdoor"
    other = dict(sample, setting="indoor", people_count=1, age_groups=["adult"])
    merged = consensus([parsed, parsed, validate_observation(other)])
    assert merged["setting"] == "outdoor" and merged["people_count"] == 2
    try:
        strip_to_json("not json")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid response was accepted")
    print("local_triage self-test: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="Advisory local Ollama pre-triage of vlog proxies")
    parser.add_argument("project", nargs="?", help="ingest/project directory")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, help="maximum clips to process this run")
    parser.add_argument("--clip", help="only one capture-time key, e.g. 171740")
    parser.add_argument("--force", action="store_true", help="refresh matching cached records")
    parser.add_argument("--dry-run", action="store_true", help="show planned work without model calls or writes")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not args.project:
        parser.error("project is required unless --self-test is used")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")

    project = Path(args.project).expanduser().resolve()
    output_path = project / "local_triage.json"
    previous = load_json(output_path, {})
    previous_records = previous.get("clips", {}) if isinstance(previous, dict) else {}
    if not isinstance(previous_records, dict):
        previous_records = {}

    planned: list[tuple[dict[str, Any], Path, dict[str, Any]]] = []
    skipped = 0
    for clip in clips_from_index(project):
        key = str(clip.get("t", ""))
        if args.clip and key != args.clip:
            continue
        proxy_value = clip.get("proxy")
        proxy = (project / proxy_value).resolve() if isinstance(proxy_value, str) else Path()
        if not proxy.is_file():
            print(f"WARN {key}: missing proxy {proxy}", file=sys.stderr)
            continue
        fingerprint = proxy_fingerprint(proxy, args.model)
        old = previous_records.get(key)
        if not args.force and isinstance(old, dict) and old.get("fingerprint") == fingerprint and old.get("status") == "ok":
            skipped += 1
            continue
        planned.append((clip, proxy, fingerprint))
        if args.limit is not None and len(planned) >= args.limit:
            break

    if args.dry_run:
        print(json.dumps({
            "project": str(project), "model": args.model, "planned": len(planned),
            "skipped_cached": skipped, "clips": [str(item[0].get("t")) for item in planned],
        }, ensure_ascii=False, indent=2))
        return 0

    if not planned:
        print(json.dumps({
            "output": str(output_path), "processed": 0, "skipped_cached": skipped,
            "warning": "No model calls were needed; cached results remain advisory only.",
        }, ensure_ascii=False, indent=2))
        return 0

    check_ollama_model(args.model)

    document = {
        "schema_version": 1,
        "advisory_only": True,
        "authority": "May prioritize paid-model review; must never exclude footage or decide story.",
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "clips": dict(previous_records),
    }
    frames_root = project / "local_triage_frames"
    for number, (clip, proxy, fingerprint) in enumerate(planned, 1):
        key = str(clip.get("t", ""))
        duration = float(clip.get("dur") or 0)
        observations = []
        errors = []
        print(f"[{number}/{len(planned)}] {key} {proxy.name}", flush=True)
        for sample_number, fraction in enumerate(SAMPLE_FRACTIONS, 1):
            frame = frames_root / key / f"frame_{sample_number}.jpg"
            try:
                extract_frame(proxy, duration, fraction, frame)
                observation = ollama_observe(frame, args.model)
                observations.append({"fraction": fraction, "frame": str(frame.relative_to(project)), **observation})
            except Exception as exc:  # preserve progress and uncertainty per frame
                errors.append({"fraction": fraction, "error": str(exc)[:500]})

        clean_observations = [
            {k: v for k, v in observation.items() if k not in {"fraction", "frame"}}
            for observation in observations
        ]
        status = "ok" if len(observations) == len(SAMPLE_FRACTIONS) else "partial" if observations else "failed"
        document["clips"][key] = {
            "status": status,
            "fingerprint": fingerprint,
            "observations": observations,
            "consensus": consensus(clean_observations) if clean_observations else None,
            "errors": errors,
            "analyzed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        document["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        atomic_json(output_path, document)

    print(json.dumps({
        "output": str(output_path), "processed": len(planned), "skipped_cached": skipped,
        "ok": sum(r.get("status") == "ok" for r in document["clips"].values() if isinstance(r, dict)),
        "warning": "Advisory only; verify with the paid editorial model before using any observation.",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
