"""Extract only supported photo entries from a conversation ZIP."""
import argparse
import json
import shutil
import stat
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("zip", type=Path)
p.add_argument("destination", type=Path)
args = p.parse_args()
try:
    with ZipFile(args.zip) as archive:
        found = {}
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if (name.is_absolute() or ".." in name.parts or "\\" in info.filename or
                    stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError("ZIP 中存在不安全的路径或链接")
            if info.is_dir() or any(x.startswith((".", "__MACOSX")) for x in name.parts):
                continue
            if name.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            if name in found or info.file_size > 32 * 1024**2:
                raise ValueError("ZIP 中存在重名或超过 32 MB 的照片")
            found[name] = info
        if not found or len(found) > 8 * 96 or sum(f.file_size for f in found.values()) > 8 * 35 * 1024**2:
            raise ValueError("ZIP 没有可用照片或超过容量限制")
        args.destination.mkdir(parents=True, exist_ok=False)
        try:
            for name, info in found.items():
                dest = args.destination / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, dest.open("xb") as output:
                    shutil.copyfileobj(source, output)
        except Exception:
            shutil.rmtree(args.destination)
            raise
    print(json.dumps(sorted(str(args.destination.resolve() / n) for n in found), ensure_ascii=False))
except (OSError, ValueError) as error:
    p.exit(2, f"{error}\n")
