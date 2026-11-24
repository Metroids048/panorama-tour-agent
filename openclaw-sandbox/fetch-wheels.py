"""Fetch pinned Linux wheels directly and verify their published sizes and SHA256."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

VERSIONS = {"pip": "25.3", "numpy": "2.0.2", "pillow": "11.3.0", "opencv-python-headless": "5.0.0.93"}
args = argparse.ArgumentParser(description=__doc__)
args.add_argument("destination", type=Path)
args.add_argument("--arch", choices=("aarch64", "x86_64"), default="aarch64")
opts = args.parse_args()
opts.destination.mkdir(parents=True, exist_ok=True)
http = build_opener(ProxyHandler({}))
lock = []
for name, version in VERSIONS.items():
    with http.open(f"https://pypi.org/pypi/{name}/{version}/json", timeout=15) as response:
        data = json.load(response)
    candidates = [item for item in data["urls"] if item["filename"].endswith(".whl") and
                  (item["filename"].endswith("py3-none-any.whl") if name == "pip" else
                   opts.arch in item["filename"] and "manylinux" in item["filename"] and
                   ("cp312-cp312" in item["filename"] or "abi3" in item["filename"]))]
    wheel = min(candidates, key=lambda item: ("manylinux_2_17" not in item["filename"], item["size"]))
    dest = opts.destination / wheel["filename"]
    expected = wheel["digests"]["sha256"]
    downloader = Path("~/.local/bin/aiw-download-direct").expanduser()
    if not dest.is_file() or hashlib.sha256(dest.read_bytes()).hexdigest() != expected:
        subprocess.run([str(downloader), wheel["url"], str(dest), "--sha256", expected,
                        "--size", str(wheel["size"])], check=True)
    if dest.stat().st_size != wheel["size"] or hashlib.sha256(dest.read_bytes()).hexdigest() != expected:
        raise ValueError(f"Wheel does not match PyPI: {dest.name}")
    lock.append({k: wheel[k] for k in ("filename", "size", "url")} | {"sha256": expected})
(opts.destination / "lock.json").write_text(json.dumps(lock, indent=2) + "\n")
