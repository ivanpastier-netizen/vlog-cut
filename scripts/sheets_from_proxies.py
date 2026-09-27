#!/usr/bin/env python3
"""sheets_from_proxies.py <ingest-dir> [frames_per_clip] — contact sheets off the PROXIES.

sheets_pil.py pulls its frames from clips_chrono's `path`, i.e. the 4K HLG
originals. A naive HLG decode looks flat and dark, so those sheets misrepresent
every exposure judgement made from them — and decoding 4K per frame is slow.
proxy_index.py has already written Rec.709 proxies, so point the sheet builder
at those instead: colour-honest and an order of magnitude faster.

Emits clips_proxy.json beside the ingest dir and runs sheets_pil.py on it.
"""
import json, subprocess, sys
from pathlib import Path

D = Path(sys.argv[1]).resolve()
NTH = sys.argv[2] if len(sys.argv) > 2 else "3"
# Do NOT require shot_index.json: proxy_index writes it only after the whole
# day finishes, but the proxies land one by one — so on a 118-clip day the sheets
# would be blocked for half an hour on a file we do not need. Match proxies to
# clips_chrono by the HHMMSS the proxy filename already carries.
chrono = json.loads((D / "clips_chrono.json").read_text())
have = {f.name.split("_")[0]: f for f in (D / "proxies").glob("*.mp4")}

out, missing = [], 0
for c in chrono:
    px = have.get(c["t"][-6:])
    if px is None:
        missing += 1
        continue
    out.append(dict(idx=c["idx"], path=str(px), dur=c["dur"], t=c["t"],
                    cam=c["cam"], bits=c.get("bits"), file=c["file"]))
out.sort(key=lambda c: c["t"])
p = D / "clips_proxy.json"
p.write_text(json.dumps(out, indent=1))
print(f"{len(out)} proxied clip(s) -> {p}"
      + (f"   ({missing} without a proxy yet)" if missing else ""))
SC = Path(__file__).resolve().parent / "sheets_pil.py"
subprocess.run([sys.executable, str(SC), str(p), str(D / "sheets"), NTH], check=True)
