#!/usr/bin/env python3
"""card_resume_copy.py — resumable card offload that survives the camera dropping off.

Some action cameras in mass-storage mode power down mid-copy: shutil dies with
`OSError: [Errno 6] Device not configured` and the mounted volume disappears
entirely. A full re-probe-then-copy tool is not the right thing to retry with —
if it ffprobes every in-scope clip over USB before copying anything, each retry
pays minutes of probing again. Here the FILENAME carries the capture date+time
(which is what local_stamp() trusts first anyway), so no probing is needed to
stage correctly.

  card_resume_copy.py --since 20260101
  card_resume_copy.py --date 20260101 --date 20260102 --vol /Volumes/CARDNAME

Copies to <stage>/<YYYY-MM-DD>-ingest/<name> — a dir per CAPTURE date, matching
the per-day shot library. Writes to `<name>.part` and renames only on a verified
byte count, so an interrupted file is never mistaken for a complete one.
Waits for the volume to come back and keeps going; exits non-zero only if it
gives up. Idempotent — re-run any time.
"""
import argparse, os, re, shutil, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vlog_config as C

STAMP = re.compile(r"^(?:VID|IMG)_(\d{8})_(\d{6})", re.I)
CHUNK = 4 << 20


def card_files(vol, exts):
    dcim = Path(vol) / "DCIM"
    out = []
    if not dcim.is_dir():
        return out
    for sub in sorted(dcim.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        for f in sorted(sub.iterdir()):
            if f.suffix.lower() in exts and not f.name.startswith("."):
                out.append(f)
    return out


def wait_for_vol(vol, quiet_s, deadline):
    """Block until the volume is readable again. Returns False past the deadline."""
    said = False
    while time.time() < deadline:
        try:
            if (Path(vol) / "DCIM").is_dir():
                if said:
                    print(f"  volume back: {vol}", flush=True)
                return True
        except OSError:
            pass
        if not said:
            print(f"  waiting for {vol} to come back … (re-plug / wake the camera)",
                  flush=True)
            said = True
        time.sleep(quiet_s)
    return False


def copy_one(src, dst, deadline, vol, quiet_s):
    """Chunked copy via <dst>.part, resuming after a mid-file device drop."""
    part = dst.with_suffix(dst.suffix + ".part")
    while time.time() < deadline:
        try:
            want = src.stat().st_size
            done = part.stat().st_size if part.exists() else 0
            if done > want:                       # stale/garbage part — start over
                part.unlink(); done = 0
            if done < want:
                with open(src, "rb") as fi, open(part, "ab") as fo:
                    fi.seek(done)
                    while True:
                        b = fi.read(CHUNK)
                        if not b:
                            break
                        fo.write(b)
                        fo.flush()
            if part.stat().st_size == want:
                shutil.copystat(src, part)        # keep the capture mtime
                part.replace(dst)
                return True
            print(f"  short read on {src.name} "
                  f"({part.stat().st_size}/{want}) — retrying", flush=True)
        except OSError as e:
            print(f"  {src.name}: {e.__class__.__name__} {e.errno} — "
                  f"device dropped, will resume", flush=True)
        if not wait_for_vol(vol, quiet_s, deadline):
            return False
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vol", default=C.get("card_volume") or None,
                    required=not C.get("card_volume"),
                    help="mounted camera card volume (config key card_volume)")
    ap.add_argument("--stage", default=str(C.INGEST_ROOT))
    ap.add_argument("--date", action="append", help="YYYYMMDD (repeatable)")
    ap.add_argument("--since", help="YYYYMMDD and later")
    ap.add_argument("--ext", default="mp4,mov,jpg,jpeg,dng,heic",
                    help="comma list; LRV (the camera's own proxies) is never copied")
    ap.add_argument("--timeout-min", type=float, default=180.0)
    ap.add_argument("--poll", type=float, default=5.0)
    a = ap.parse_args()

    exts = {"." + e.strip().lower().lstrip(".") for e in a.ext.split(",") if e.strip()}
    stage = Path(a.stage)
    deadline = time.time() + a.timeout_min * 60

    if not wait_for_vol(a.vol, a.poll, deadline):
        sys.exit(f"{a.vol} never appeared")

    def in_scope(name):
        m = STAMP.match(name)
        if not m:
            return False, None
        d = m.group(1)
        if a.date and d not in set(a.date):
            return False, d
        if a.since and d < a.since:
            return False, d
        return True, d

    files, skipped = [], 0
    for f in card_files(a.vol, exts):
        ok, d = in_scope(f.name)
        if ok:
            files.append((f, d))
        elif d:
            skipped += 1
    print(f"{len(files)} file(s) in scope on {a.vol}  "
          f"({skipped} outside the date filter — left on the card)")

    todo = []
    for f, d in files:
        dst = stage / f"{d[:4]}-{d[4:6]}-{d[6:]}-ingest" / f.name
        try:
            if dst.exists() and dst.stat().st_size == f.stat().st_size:
                continue
        except OSError:
            pass
        todo.append((f, dst))
    print(f"{len(files) - len(todo)} already staged, {len(todo)} to copy")

    done = fail = 0
    for i, (src, dst) in enumerate(todo, 1):
        dst.parent.mkdir(parents=True, exist_ok=True)
        if copy_one(src, dst, deadline, a.vol, a.poll):
            done += 1
        else:
            fail += 1
            print(f"  GAVE UP on {src.name}", flush=True)
        if i % 10 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}  (copied {done}, failed {fail})", flush=True)

    print(f"\ncopied {done}, failed {fail}, target {len(todo)}")
    by = {}
    for _, dst in todo:
        by.setdefault(dst.parent, 0)
    for d in sorted(by):
        n = len(list(d.glob("*"))) if d.is_dir() else 0
        print(f"  {d}: {n} files")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
