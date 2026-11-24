"""Prepare a chat upload and numbered visual boards in one sandbox call."""
import argparse
from hashlib import file_digest
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from time import perf_counter

from PIL import Image


def helper(name, *args):
    run = subprocess.run([sys.executable, Path(__file__).with_name(name), *args],
                         capture_output=True, text=True, check=False)
    if run.returncode:
        raise ValueError(run.stderr.strip().splitlines()[-1] if run.stderr else name)
    return json.loads(run.stdout)


def prepare(task, uploads, receipt=None, names=None):
    if task.exists():
        raise ValueError("这个任务目录已存在；继续使用原任务的照片，或为新上传使用新目录。")
    task.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=task.parent, prefix=".intake-") as staging:
        staging = Path(staging)
        build = staging / "bundle"
        photos, sheets = build / "photos", build / "sheets"
        if len(uploads) == 1 and uploads[0].suffix.lower() == ".zip":
            helper("unpack_photos.py", uploads[0], photos)
        else:
            if any(p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"} for p in uploads):
                raise ValueError("请上传一个照片 ZIP，或直接上传 JPG、PNG、WebP 照片。")
            photos.mkdir(parents=True)
            for i, (path, name) in enumerate(zip(uploads, names or [p.name for p in uploads]), 1):
                shutil.copyfile(path, photos / f"{i:02d}-{Path(name).name}")
        rows = helper("inspect_spheres.py", photos, sheets)
        boards = []
        for start in range(0, len(rows), 4):
            group = rows[start:start+4]
            columns = min(len(group), 2)
            cell_width, cell_height = 1144, 612
            board = Image.new("RGB", (columns * cell_width,
                                      ((len(group) + columns - 1) // columns) * cell_height), "#0a1815")
            for index, row in enumerate(group):
                with Image.open(row["preview"]) as image:
                    image.thumbnail((cell_width - 8, cell_height - 8), Image.Resampling.LANCZOS)
                    board.paste(image, ((index % columns) * cell_width, (index // columns) * cell_height))
            name = f"board-{start//4+1}.jpg"
            board.save(build / name, quality=89)
            boards.append(str(task / name))
        result = {"photos": [{"number": i, "file": "photos/" + row["file"],
                              "width": row["width"], "height": row["height"],
                              "kind": "invalid" if row.get("error") else "map" if abs(row["width"] - 2 * row["height"]) > max(2, row["width"] * 0.002) and re.search(
                                  r"地图|平面图|楼层图|校园图|(?:^|[\W_])(?:map|plan|floor[\W_]*plan)(?:[\W_]|$)",
                                  row["file"], re.I) else "photo",
                              "detail": str(task / "sheets" / f"{i}.jpg"),
                              **({"error": row["error"]} if row.get("error") else {})}
                             for i, row in enumerate(rows, 1)],
                  "boards": boards}
        if receipt is not None:
            result["receipt"] = receipt
        (build / "intake.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        build.rename(task)
    return result


def verify_originals(receipt_path, receipt):
    uploads, names = [], []
    for entry in receipt["files"]:
        if entry["status"] != "received":
            continue
        try:
            image = Path(entry["path"]).resolve(strict=True)
            if image.parent != receipt_path.parent or image.stat().st_size != entry["bytes"]:
                raise ValueError("bad path or size")
            with image.open("rb") as source:
                if file_digest(source, "sha256").hexdigest() != entry["sha256"]:
                    raise ValueError("bad digest")
        except (OSError, ValueError):
            entry.update(status="missing", reason="本轮原件校验失败，请只重发这一件")
            continue
        uploads.append(image)
        names.append(entry["name"])
    return uploads, names


def prepare_receipt(task, receipt_path):
    receipt_path = receipt_path.resolve()
    root = Path(__file__).resolve().parent
    inbound = root / "media" / "inbound"
    if not receipt_path.is_relative_to(inbound):
        raise ValueError("收件回执不属于这条消息。")
    match = re.fullmatch(r"([a-f0-9-]{36})/([a-f0-9-]{36})/receipt\.json",
                         receipt_path.relative_to(inbound).as_posix())
    if not match or task.resolve() != root / "tasks" / match[1] / "uploads" / match[2]:
        raise ValueError("收件回执不属于这条消息。")
    receipt = {"path": str(receipt_path), "files": []}
    if receipt_path.exists():
        receipt.update(json.loads(receipt_path.read_text(encoding="utf-8")))
        if receipt.get("session_id") != match[1] or receipt.get("run") != match[2]:
            raise ValueError("收件回执与当前任务不匹配。")
        receipt["path"] = str(receipt_path)
    prior = task / "intake.json"
    if prior.is_file():
        result = json.loads(prior.read_text(encoding="utf-8"))
        result["receipt"]["path"] = str(receipt_path)
        return result
    started = perf_counter()
    uploads, names = verify_originals(receipt_path, receipt)
    verified = perf_counter()
    if not uploads:
        return {"receipt": receipt, "photos": [], "boards": [],
                "next": "本轮没有可读原件。保留已有项目和草稿，请只重发缺失的 JPG，也可只把这一张打成 ZIP。"}
    try:
        result = prepare(task, uploads, receipt, names)
        result["timings_ms"] = {"verify_originals": round((verified - started) * 1000),
                                "prepare_boards": round((perf_counter() - verified) * 1000)}
        return result
    except ValueError as error:
        return {"receipt": receipt, "photos": [], "boards": [], "next": str(error)}


def save_first_look(task, result, mode, title, purpose, *, project_key=None):
    from project import Project

    receipt = result["receipt"]
    key = project_key or "first-look-" + receipt["run"]
    port = "8091" if Path(__file__).resolve().parent == Path("/oasn") else "18792"
    args = ("--project", key, "--port", port)
    project = Project(key)
    parent = None
    prior = None
    seen = set()
    if (project.root / "latest").is_file():
        prior, revision = project.read()
        workflow = prior.get("workflow", {})
        if workflow.get("session") != receipt["session"]:
            raise ValueError("补图项目必须属于当前会话。")
        if any(batch["run"] == receipt["run"] for batch in workflow["batches"]):
            saved = helper("app.py", *args, "--resume", "--parent", revision.name)
            return first_look_result(task, receipt, saved, continued=bool(project_key))
        parent = revision.name
        seen = {file.split("/")[1] for station in prior["stations"]
                for file in station["files"] + station.get("alternates", [])}

    photos = [p for p in result["photos"] if p.get("kind") == "photo"]
    if not photos or any(abs(p["width"] - 2 * p["height"]) > max(2, p["width"] * 0.002) for p in photos):
        raise ValueError("这批图需要先核对球面与普通照片，已保留原件。")
    frames_path = Path(__file__).with_name("first_look_frames.json")
    frames = json.loads(frames_path.read_text(encoding="utf-8")) if mode == "scenes" and frames_path.is_file() else {}
    stations = []
    entry = None
    bind_openings = False
    for photo in photos:
        with (task / photo["file"]).open("rb") as source:
            digest = file_digest(source, "sha256").hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        number = photo["number"]
        station_id = f"photo-{receipt['run'][:8]}-{number}" if parent else f"photo-{number}"
        station = {"id": station_id, "name": f"照片 {number}",
                   "files": [photo["file"]], "spherical": True}
        frame = frames.get(digest)
        if frame:
            station["opening"] = frame["opening"]
            bind_openings = True
            if entry is None and re.search(r"从\s*" + re.escape(frame["start"]) + r"\s*(?:到|去|往|出发|开始)",
                                           purpose or ""):
                entry = station["id"]
        stations.append(station)
    if len(stations) + (len(prior["stations"]) if prior else 0) > 32:
        raise ValueError("本批超过 32 个不同画面，已保留全部原件，请按区域拆成多份导览。")

    manifest = prior or {"title": title or "本批全景", "stations": [], "first_look": True}
    manifest["stations"].extend(stations)
    if purpose and not prior:
        manifest["purpose"] = purpose
    if prior:
        if "venue" in manifest and stations:
            levels = manifest["venue"]["levels"]
            if not any(level["id"] == "unplaced" for level in levels):
                levels.append({"id": "unplaced", "name": "待确认楼层"})
            manifest["venue"]["places"].extend({"station": s["id"], "level": "unplaced"} for s in stations)
    elif mode == "venue":
        manifest["venue"] = {
            "coverage": "本批实拍 · 楼层和分区待确认",
            "levels": [{"id": "unplaced", "name": "待确认楼层"}],
            "places": [{"station": s["id"], "level": "unplaced"} for s in stations],
        }
    else:
        manifest["visitor_route"] = True
        if entry:
            manifest["entry"] = entry
    if parent:
        args += ("--parent", parent)
    bind = ("--bind-spheres",) if bind_openings else ()
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=task, suffix=".json") as draft:
        json.dump(manifest, draft, ensure_ascii=False)
        draft.flush()
        saved = helper("app.py", *args, "--manifest", draft.name, "--intake", receipt["path"], *bind)
    return first_look_result(task, receipt, saved, continued=bool(project_key))


def first_look_result(task, receipt, saved, *, continued=False):
    from project import atomic

    first = {k: saved[k] for k in ("project", "revision", "manifest", "review_url", "preview_url",
                                  "recover_url", "order", "grade")}
    first["station_views"] = {s["id"]: s["view_revision"] for s in saved["stations"]}
    manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
    if continued or manifest.get("first_look") is True or first["project"] == "first-look-" + receipt["session_id"]:
        if (manifest.get("project"), manifest.get("revision")) != (first["project"], first["revision"]):
            raise ValueError("首看清单与保存回执不一致。")
        manifest.pop("first_look", None)
        template = task / f'arrangement-{first["revision"]}.json'
        atomic(template, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        first["continuation"] = {"project": first["project"], "parent": first["revision"],
                                 "manifest": str(template), "intake": receipt["path"]}
    return first


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("uploads", nargs="*", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--first-look", choices=["scenes", "venue"],
                        help="save a viewing-only draft of user-confirmed full spheres in a new task")
    parser.add_argument("--title", help="project title for the viewing draft")
    parser.add_argument("--purpose", help="purpose retained with the viewing draft")
    args = parser.parse_args()
    if bool(args.receipt) == bool(args.uploads):
        parser.error("Provide either an upload receipt or explicit uploads.")
    if args.first_look and not args.receipt:
        parser.error("首看草稿需要本轮聊天原件回执。")
    try:
        started = perf_counter()
        task = args.task.resolve()
        result = prepare_receipt(task, args.receipt) if args.receipt else prepare(task, args.uploads)
        if args.first_look and result["photos"]:
            save_started = perf_counter()
            try:
                result["first_look"] = save_first_look(task, result, args.first_look, args.title, args.purpose)
            except (OSError, ValueError) as error:
                result["first_look_note"] = str(error)
            result.setdefault("timings_ms", {})["first_look_save"] = round((perf_counter() - save_started) * 1000)
        result.setdefault("timings_ms", {})["local_total"] = round((perf_counter() - started) * 1000)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError) as error:
        parser.exit(2, f"{error}\n")
