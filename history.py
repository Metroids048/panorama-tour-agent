"""Read-only index of frozen workbench deliveries and their exact input files."""
import json
import io
from pathlib import Path

from PIL import Image

from delivery import quality
from project import PROJECTS, ID, REV, SOURCE, Project, sha


def read_version(key, revision):
    project = Project(key)
    manifest, base = project.read(revision)
    receipt = json.loads((base / "receipt.json").read_text())
    if not (base / "tour.html").is_file():
        raise FileNotFoundError("缺少冻结网页")
    return project, manifest, receipt, base


def describe(project, manifest, receipt, base, detail=False):
    specs = {s["id"]: s for s in manifest["stations"]}
    stations = []
    for result in receipt["stations"]:
        spec = specs[result["id"]]
        files = spec["files"] + spec.get("alternates", [])
        view = dict(result.get("view") or {})
        if view and spec.get("spherical") and not view.get("width"):
            # Legacy spherical processing capped the texture at 3840 × 1920.
            if SOURCE.fullmatch(files[0]) and Path(files[0]).name not in {".", ".."}:
                try:
                    with Image.open(project.root / files[0]) as im:
                        view["width"], view["height"] = min(im.width, 3840), min(im.height, 1920)
                except (OSError, ValueError, Image.DecompressionBombError):
                    pass
        row = {**{k: result[k] for k in ("id", "name", "kind")}, "view": view, "count": len(files)}
        if detail:
            row.update(files=[{"name": Path(f).name, "used": Path(f).name in result.get("used_files", [])}
                              for f in files],
                       spherical=spec.get("spherical", False), capture=bool(spec.get("capture")),
                       note=result.get("reason"), source=spec.get("source"),
                       source_archive=bool(spec.get("source_archive")))
        stations.append(row)
    out = dict(project=project.key, revision=base.name, title=manifest.get("title") or "空间预览",
               time=(base / "receipt.json").stat().st_mtime, stations=len(stations),
               photos=sum(s["count"] for s in stations), grade=receipt.get("grade") or quality(stations, receipt["edges"]))
    out.update(editable=bool(stations and (manifest.get("visitor_route") or manifest.get("venue"))),
               next_action=manifest.get("next_action"), native=bool(manifest.get("workflow")),
               first_look=manifest.get("first_look") is True,
               fulfillment=receipt.get("fulfillment"),
               archive_notice=manifest.get("archive_notice"))
    if detail:
        session = (manifest.get("workflow") or {}).get("session")
        if isinstance(session, str) and session.startswith("agent:space-preview:"):
            out["session"] = session
        out.update(entry=receipt["entry"], inputs=stations, sha256=receipt["sha256"], parent=manifest.get("parent"),
                   route=manifest.get("route_evidence"), attribution=manifest.get("attribution"))
        if manifest.get("venue"):
            out["plans"] = [{"level": l["id"], "name": Path(l["plan"]).name, "source": l["source"],
                             "url": f"/api/history/{project.key}/{base.name}/plans/{l['id']}"}
                            for l in manifest["venue"]["levels"] if l.get("plan")]
    return out


def listing():
    result = []
    for folder in PROJECTS.iterdir() if PROJECTS.exists() else ():
        if not folder.is_dir() or not ID.fullmatch(folder.name) or not (folder / "versions").is_dir():
            continue
        versions = []
        for base in (folder / "versions").iterdir():
            if not REV.fullmatch(base.name):
                continue
            try:
                versions.append(describe(*read_version(folder.name, base.name)))
            except (OSError, ValueError, TypeError, KeyError, IndexError):
                continue
        if versions:
            versions.sort(key=lambda v: v["time"], reverse=True)
            latest = (folder / "latest").read_text().strip() if (folder / "latest").is_file() else ""
            result.append({"versions": versions, "latest": latest or versions[0]["revision"]})
    return sorted(result, key=lambda p: p["versions"][0]["time"], reverse=True)


def response(path):
    if path == "/api/history":
        return 200, json.dumps(listing(), ensure_ascii=False), "application/json"
    _, _, _, key, revision, *parts = path.split("/")
    project, manifest, receipt, base = read_version(key, None if revision == "latest" and not parts else revision)
    if not parts:
        return 200, json.dumps(describe(project, manifest, receipt, base, True), ensure_ascii=False), "application/json"
    if parts == ["tour.html"]:
        data = (base / "tour.html").read_bytes()
        if sha(data) != receipt["sha256"]:
            raise ValueError("历史网页与冻结回执不符")
        return 200, data, "text/html"
    if len(parts) == 2 and parts[0] == "plans":
        level = next((l for l in manifest.get("venue", {}).get("levels", [])
                      if l["id"] == parts[1] and l.get("plan")), None)
        if not level:
            raise FileNotFoundError("无此楼层平面")
        data = project.asset(level["plan"])
        with Image.open(io.BytesIO(data)) as image:
            return 200, data, {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[image.format]
    if parts == ["route-card.jpg"] and receipt.get("route_card_sha256"):
        data = (base / "route-card.jpg").read_bytes()
        if sha(data) != receipt["route_card_sha256"]:
            raise ValueError("实景路线图与冻结回执不符")
        return 200, data, "image/jpeg"
    if len(parts) == 2 and parts[0] == "source-archive" and parts[1].isascii() and parts[1].isdecimal():
        source = manifest["stations"][int(parts[1])]["source_archive"]
        data = project.asset(source)
        if data.startswith(b"PK\x03\x04"):
            return 200, data, "application/zip"
    if len(parts) == 3 and parts[0] == "photo" and all(p.isascii() and p.isdecimal() for p in parts[1:]):
        _, station, photo = parts
        spec = manifest["stations"][int(station)]
        source = (spec["files"] + spec.get("alternates", []))[int(photo)]
        data = project.asset(source)
        mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else (
            "image/jpeg" if data.startswith(b"\xff\xd8") else
            "image/webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None)
        if mime:
            return 200, data, mime
    raise FileNotFoundError("历史记录资源不存在")
