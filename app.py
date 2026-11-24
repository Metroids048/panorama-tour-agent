"""Local authoring endpoint and portable HTML export. No external services."""
import argparse
import base64
import fcntl
import html
import io
import json
import math
import os
import re
import secrets
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

from processing import prepare_station
from presentation import enhance_sphere
import history
from delivery import check_delivery, download_scope, fulfillment, quality
from fixed_route import access_issue, public_route_page, route_info
from project import Project, RELEASE, REV, MAX_STATIONS, atomic, label as title, sha, summary
from retention import ensure_download

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "本轮成果-20261108"
RESEARCH = EVIDENCE / "普通照片全景拼接-实跑-20261109"
LAKE = EVIDENCE / "v2" / "stitching"
MAX_BODY = 48 * 1024 * 1024
MAX_FILE = 32 * 1024 * 1024
DOWNLOAD_DIR = ROOT / "openclaw-workspace" / ".downloads"
SANDBOX = os.environ.get("SPACE_PREVIEW_SANDBOX") == "1"
VISITOR_SAMPLE = ROOT / "evidence" / "三站认路对照" / "B-第一次来认路.html"
SAMPLES = {**{f"/sample/phone-{i}.jpg": RESEARCH / n for i, n in enumerate(("room14.jpg", "room15.jpg"), 1)},
           **{f"/sample/sphere-{i}.jpg": EVIDENCE / "v3" / "photos" / f"station-{i:02}.jpg" for i in range(1, 6)},
           **{f"/sample/cave-{j}-{i}.jpg": ROOT / "samples" / "cave" / f"{j}-{i}.jpg" for j in (1,2,3) for i in (1,2,3)},
           **{f"/sample/lake-{i}.jpg": LAKE / f"boat{i}.jpg" for i in range(1, 7)},
           "/sample/car-1.jpg": RESEARCH / "car1.jpg", "/sample/car-2.jpg": RESEARCH / "car2.jpg",
           "/sample/unrelated.jpg": LAKE / "budapest1.jpg"}
FILES = {"/": ("index.html", "text/html"), "/pannellum.js": ("pannellum.js", "text/javascript"),
         "/viewer.js": ("viewer.js", "text/javascript"), "/editor.js": ("editor.js", "text/javascript"),
         "/history.js": ("history.js", "text/javascript"),
         "/pannellum.css": ("pannellum.css", "text/css"), "/viewer.css": ("viewer.css", "text/css"),
         "/workbench.css": ("workbench.css", "text/css")}
SAMPLES["/sample/visitor-cover.jpg"] = ROOT / "samples" / "visitor-cover.jpg"


def station_from_upload(data):
    capture = data.get("capture")
    spherical = data.get("spherical") is True
    files = [(f.get("name") or f"照片 {i}", base64.b64decode(f["data"], validate=True))
             for i, f in enumerate(data.get("files", []), 1)]
    name = title(data.get("name", ""), "未命名点位")
    if not data.get("project"):
        return enhance_sphere(prepare_station(name, files, spherical, capture), files, spherical and capture is None)
    project = Project(data["project"])
    source = list(data.get("sources", [])) + [project.original(n, b) for n, b in files]
    record = project.original(data.get("capture_name", "capture.json"),
                              json.dumps(capture, ensure_ascii=False).encode()) if capture is not None else data.get("capture_source")
    spec = {"files": source, "spherical": spherical}
    if record:
        spec["capture"] = record
    return dict(project.station(spec, prepare_station), name=name, source=spec)


def validate_links(tour):
    def between(value, low, high):
        return type(value) in (int, float) and math.isfinite(value) and low <= value <= high

    stations = tour["stations"]
    venue = tour.get("venue")
    allowed = {(c["from"], c["to"]) for c in venue["connections"] if not c.get("pending")} if venue else set()
    branched = "route_connections" in tour
    if branched:
        connections = tour["route_connections"]
        if (venue or tour.get("fixed_route") or tour.get("forward_only") or not tour.get("visitor_route")
                or not isinstance(connections, list) or not connections):
            raise ValueError("分支通道需用于可往返实景导览")
        allowed = set()
        for connection in connections:
            if not isinstance(connection, dict):
                raise ValueError("分支通道配置无效")
            pair = connection.get("from"), connection.get("to")
            if (any(type(v) is not int or not 0 <= v < len(stations) for v in pair)
                    or pair[0] == pair[1] or pair in allowed):
                raise ValueError("分支通道须连接不同的已知站，且不得重复")
            allowed.add(pair)
    entry = tour.get("entry", 0)
    if venue and not stations and entry is None:
        return
    if type(entry) is not int or not 0 <= entry < len(stations):
        raise ValueError("入口站无效")
    for i, station in enumerate(stations):
        links = station.get("links", [])
        if not isinstance(links, list) or len(links) > 18:
            raise ValueError("每站的箭头配置无效")
        pano = station.get("panorama")
        for link in links:
            to = link["to"]
            if type(to) is not int or not 0 <= to < len(stations) or to == i:
                raise ValueError("画面箭头须指向另一已知站")
            if venue and ((i, to) not in allowed or link.get("cut") or "photo" in link):
                raise ValueError("场馆箭头须绑定已核对的方向连接和源站全景")
            if branched and ((i, to) not in allowed or link.get("cut") or "photo" in link):
                raise ValueError("分支箭头须绑定已声明的实景方向")
            if not venue and not branched and abs(to - i) != 1:
                raise ValueError("画面箭头须指向相邻站")
            if link.get("cut") is True:
                if set(link) != {"to", "cut"}:
                    raise ValueError("直接切站无需指定画面位置")
                continue
            if "photo" in link:
                photo = link["photo"]
                valid = (type(photo) is int and 0 <= photo < len(station["originals"])
                         and between(link["x"], 0, 1) and between(link["y"], 0, 1))
            else:
                valid = (pano and between(link["yaw"], -pano["haov"] / 2, pano["haov"] / 2)
                         and between(link["pitch"], pano["vOffset"] - pano["vaov"] / 2,
                                     pano["vOffset"] + pano["vaov"] / 2))
            if not valid:
                raise ValueError(f"第 {i + 1} 站的箭头超出画面")


def portable(tour):
    validate_links(tour)
    head = html.escape(tour["title"])
    is_venue = "venue" in tour
    stations, edges = [], []
    for i, s in enumerate(tour["stations"]):
        view = {k: v for k, v in (s.get("panorama") or {}).items() if k != "image"}
        if view and not view.get("width"):
            with Image.open(io.BytesIO(base64.b64decode(s["panorama"]["image"].split(",", 1)[1]))) as image:
                view["width"], view["height"] = image.size
        stations.append(dict(id=i, name=s["name"], view=view))
        edges.extend({"from": i, "to": e["to"], "status": "cut" if e.get("cut") else "confirmed",
                      "photo_only": "photo" in e}
                     for e in s.get("links", []))
    grade = tour.get("grade") or quality(stations, edges, entry=tour.get("entry"),
        required=[(c["from"], c["to"]) for c in tour["route_connections"]] if "route_connections" in tour else None)
    payload = json.dumps(dict(tour, grade=grade), ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    note = html.escape(grade["label"])
    detail = " ".join(grade["reasons"])
    credit = ""
    if tour.get("attribution"):
        if tour.get("visitor_route") or tour.get("venue"):
            credit = '<p class="source-credit">' + html.escape(str(tour["attribution"])) + '</p>'
        else:
            detail += (" " if detail else "") + str(tour["attribution"])
    detail = '<p class="muted">' + html.escape(detail) + '</p>' if detail and not tour.get("visitor_route") else ""
    header_class = " class=venue-header" if is_venue else " class=visitor-header" if tour.get("visitor_route") else ""
    heading = f'<h1>{head}</h1>' if is_venue else f'<div class="eyebrow">{note}</div><h1>{head}</h1>{detail}'
    if tour.get("archive_notice") and not is_venue:
        heading = (f'<div class="eyebrow">档案试走</div><h1>{head}</h1>'
                   f'<p class="archive-notice">{html.escape(tour["archive_notice"])}</p>{detail}')
    if credit and is_venue:
        credit = '<details class="venue-credit"><summary>照片来源与许可</summary>' + credit + '</details>'
    read = lambda name: (ROOT / name).read_text(encoding="utf-8")
    info = tour.get("fixed_route", {})
    if (info.get("public_photos") and grade["route_ready"] and not grade["reverse"]
            and not info["valid_until"] and not any(s.get("panorama") for s in tour["stations"])):
        return (f'<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1"><link rel="icon" href="data:,"><title>{head}</title>'
                f'<style>{read("viewer.css")}</style><header class="visitor-header walk-header"><div>{heading}</div></header>'
                f'{public_route_page(tour)}<footer class="body muted">{credit}</footer></html>')
    return (f'<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><link rel="icon" href="data:,"><title>{head}</title>'
            f'<style>{read("pannellum.css")}\n{read("viewer.css")}</style>'
            f'<header{header_class}><div>{heading}</div></header>'
            f'<main class="body" id="tour"></main><script>{read("pannellum.js")}</script>'
            f'<script>{read("viewer.js")}</script><script>window.activeTour=mountTour(document.getElementById("tour"),{payload});</script>'
            f'<footer class="body muted">{credit}<small>Pannellum · <details><summary>组件许可</summary><pre style="white-space:pre-wrap">{html.escape(read("COPYING"))}</pre></details></small></footer></html>')


def visitor_sample():
    if VISITOR_SAMPLE.is_file():
        return VISITOR_SAMPLE.read_bytes()

    stations = []
    scenes = (
        ("入口", "#446b7f", (1,)),
        ("走廊", "#876641", (0, 2)),
        ("展厅", "#45745c", (1,)),
    )
    for name, color, neighbors in scenes:
        image = Image.new("RGB", (1024, 512), color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 280, 1023, 511), fill="#29343a")
        for x in range(0, 1024, 128):
            draw.line((512, 280, x, 511), fill="#77878c", width=3)
        links = []
        for neighbor in neighbors:
            yaw = 24 if neighbor > len(stations) else -24
            center = round(512 + 1024 * yaw / 360)
            draw.rectangle((center - 24, 218, center + 24, 295), fill="#263238", outline="#f0dbc0", width=6)
            links.append({"to": neighbor, "yaw": yaw, "pitch": 0})
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        data = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        stations.append({
            "name": name,
            "panorama": {"image": data, "width": 1024, "height": 512, "haov": 360, "vaov": 180, "vOffset": 0},
            "originals": [{"image": data, "name": name}],
            "opening": {"yaw": 0, "pitch": 0, "hfov": 70},
            "links": links,
        })
    return portable({
        "title": "全景导览 · 三站示例",
        "visitor_route": True,
        "entry": 0,
        "attribution": "程序生成的示意场景",
        "stations": stations,
    }).encode("utf-8")


def cli_tour(manifest):
    data = json.loads(manifest.read_text())
    if "venue" in data:
        raise ValueError("场馆总览须从保存版本导出")
    raw = data["stations"]
    if not 1 <= len(raw) <= MAX_STATIONS:
        raise ValueError("导览应有 1–32 站")
    result = {"title": title(data.get("title", ""), "空间预览"), "stations": []}
    for i, station in enumerate(raw, 1):
        capture_path = station.get("capture")
        capture = json.loads((manifest.parent / capture_path).read_text()) if capture_path else None
        files = []
        for filename in station["files"]:
            p = manifest.parent / filename
            if p.stat().st_size > MAX_FILE:
                raise ValueError(f"{p.name}：每张原图最多 32 MB")
            files.append((p.name, p.read_bytes()))
        name = title(station.get("name", ""), f"点位 {i}")
        spherical = station.get("spherical") is True
        item = enhance_sphere(prepare_station(name, files, spherical, capture), files, spherical and capture is None)
        if station.get("use_originals") and item["panorama"]:
            item.update(panorama=None, kind="photos", reason="本点位展示原图。")
        item["links"] = station.get("links", [])
        result["stations"].append(item)
    return result


class WorkbenchServer(ThreadingHTTPServer):
    request_queue_size = 32


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, mime="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime != "image/jpeg" else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def download(self, head=False):
        target = urlsplit(self.path)
        match = re.fullmatch(r"/download/([0-9a-f]{32})/tour\.html", target.path)
        if not match:
            return False
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.send_error(403)
            return True
        try:
            file = (DOWNLOAD_DIR / (match[1] + ".html")).open("rb")
        except FileNotFoundError:
            self.send_error(404)
            return True
        with file:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            query = parse_qs(target.query)
            name = query.get("name", [""])[0]
            disposition = 'attachment; filename="space-tour.html"'
            if name:
                disposition += "; filename*=UTF-8''" + quote(download_name(name, query.get("kind", [""])[0]), safe="")
            self.send_header("Content-Disposition", disposition)
            self.send_header("Content-Length", str(file.seek(0, 2)))
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if not head:
                file.seek(0)
                for chunk in iter(lambda: file.read(65536), b""):
                    self.wfile.write(chunk)
        return True

    def do_HEAD(self):
        if not self.download(head=True):
            self.send_error(404)

    def do_GET(self):
        if self.download():
            return
        path = urlsplit(self.path).path
        if path in FILES:
            name, mime = FILES[path]
            self.send(200, (ROOT / name).read_bytes(), mime)
        elif path == "/api/version":
            self.send(200, json.dumps({"release": RELEASE}))
        elif path == "/sample/visitor-tour.html":
            self.send(200, visitor_sample(), "text/html")
        elif path == "/api/history" or path.startswith("/api/history/"):
            if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                self.send_error(403)
                return
            try:
                self.send(*history.response(path))
            except (OSError, ValueError, KeyError, TypeError, IndexError):
                self.send_error(404)
        elif path in SAMPLES and SAMPLES[path].is_file():
            self.send(200, SAMPLES[path].read_bytes(), "image/jpeg")
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path not in {"/api/station", "/api/export", "/api/project/load", "/api/project/save", "/api/project/download"}:
            self.send_error(404)
            return
        port = self.server.server_port
        if self.headers.get("Origin") not in {None, f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            self.send_error(403)
            return
        if self.headers.get_content_type() != "application/json":
            self.send_error(415)
            return
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 1 <= size <= MAX_BODY:
                raise ValueError("这一组文件过大，请减少照片张数或压缩后重试")
            data = json.loads(self.rfile.read(size))
            if self.path == "/api/station":
                self.send(200, json.dumps(station_from_upload(data), ensure_ascii=False))
            elif self.path == "/api/project/load":
                project = Project(data["project"])
                manifest, _ = project.read(data.get("revision"))
                draft = manifest if manifest.get("fixed_route") else dict(manifest, stations=[dict(s, use_originals=False) for s in manifest["stations"]])
                # Reopen the saved frames with the same preview-only defaults.
                tour, edges = project.assemble(draft, prepare_station, previous=draft)
                # Preview keeps the base texture available when toggling the original-photo option.
                receipt = json.loads((project.version_path(manifest["revision"]) / "receipt.json").read_text())
                if manifest.get("visitor_route") or "venue" in manifest:
                    originals = {s["id"] for s in manifest["stations"] if s.get("use_originals")}
                    confirmed = {(e["from"], e["to"]) for e in receipt["edges"] if e["status"] == "confirmed"}
                    if any(e["status"] == "stale-view" and e["from"] not in originals
                           and (e["from"], e["to"]) in confirmed for e in edges):
                        raise ValueError("这份历史导览的箭头绑定在旧版画面。可以试走和下载原保存版。")
                tour["grade"] = receipt.get("grade")
                latest = (project.root / "latest").read_text().strip()
                self.send(200, json.dumps({"manifest": manifest, "tour": tour, "edges": receipt["edges"],
                                           "confirmation": pending_confirmation(manifest, receipt),
                                           "latest_revision": latest}, ensure_ascii=False))
            elif self.path == "/api/project/download":
                if not isinstance(data.get("revision"), str) or not REV.fullmatch(data["revision"]):
                    raise ValueError("请选择要下载的保存版本")
                result = saved_result(data["project"], data["revision"], require_tour=True,
                                      require_panorama=data.get("require_panorama") is True)
                result.update(register_download(Path(result["output"]), port, result["title"]))
                self.send(200, json.dumps(result, ensure_ascii=False))
            elif self.path == "/api/project/save":
                project = Project(data["project"])
                draft, parent = data["manifest"], data.get("parent")
                if parent and (draft.get("project") != project.key or draft.get("revision") != parent):
                    raise ValueError("清单必须属于当前项目和所指定的父版")
                receipt = project.publish(draft, parent, prepare_station, portable,
                                          require_tour=data.get("require_tour") is True,
                                          require_panorama=data.get("require_panorama") is True)
                receipt_links(receipt, port)
                if not draft.get("visitor_route") and "venue" not in draft:
                    receipt.update(register_download(Path(receipt["output"]), port))
                self.send(200, json.dumps(receipt, ensure_ascii=False))
            else:
                if not 1 <= len(data["stations"]) <= MAX_STATIONS:
                    raise ValueError("导览应有 1–32 站")
                data["title"] = title(data.get("title", ""), "空间预览")
                if "venue" in data:
                    raise ValueError("场馆总览须从保存版本导出")
                data.pop("grade", None)
                self.send(200, portable(data), "text/html")
        except (ValueError, TypeError, AttributeError, KeyError, OSError) as e:
            self.send(400, json.dumps({"error": str(e) if isinstance(e, ValueError) else "无法读取提交内容"}, ensure_ascii=False))


def download_name(name, kind=""):
    clean = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "-", name[:70]).strip(" .")
    label = kind if kind in {"档案试走", "分层预览", "路线预览", "实景草稿", "看景草稿", "局部实景试走", "预览"} else "离线导览"
    return f"{clean or '空间预览'} - {label}.html"


def register_download(path, port, name=None, kind=""):
    if path.name == "tour.html" and REV.fullmatch(path.parent.name) and path.with_name("receipt.json").is_file():
        # A version being taken out must not be retired between validation and linking.
        with (path.parents[2] / ".publish.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            return _register_download(path, port, name, kind)
    return _register_download(path, port, name, kind)


def _register_download(path, port, name=None, kind=""):
    receipt_file = path.parent / "receipt.json"
    field = "download"
    if receipt_file.is_file():
        result = json.loads(receipt_file.read_text())
        manifest = json.loads((path.parent / "manifest.json").read_text())
        document = path.read_bytes()
        if sha(document) != result["sha256"]:
            raise ValueError("保存的网页与冻结回执不符")
        outcome = frozen_fulfillment(manifest, document, result)
        if not kind and not outcome["complete"]:
            raise ValueError("原始导览目标尚未完成；可取看景草稿或局部试走，不能作为完整导览取件。")
        field, kind = download_scope(outcome, kind)
    DOWNLOAD_DIR.mkdir(mode=0o700, exist_ok=True)
    if receipt_file.is_file():
        target = ensure_download(path, DOWNLOAD_DIR)
    else:
        # Samples have no project/version receipt, so stay outside task retention.
        token = secrets.token_hex(16)
        target = DOWNLOAD_DIR / (token + ".html")
        try:
            import os
            os.link(path.resolve(), target)
        except (OSError, AttributeError):
            atomic(target, path.read_bytes())
    token = target.stem
    url = f"http://127.0.0.1:{port}/download/{token}/tour.html"
    if name:
        url += "?" + urlencode({"name": name, "kind": kind})
    try:
        with urlopen(Request(url, method="HEAD"), timeout=3) as response:
            if not response.headers.get("Content-Disposition", "").startswith("attachment;"):
                raise ValueError("下载接口未返回附件")
        return {field: url, "download_name": download_name(name, kind) if name else "space-tour.html"}
    except (OSError, ValueError):
        # A retry may have reused a URL that is already present in chat.
        # A transient HEAD failure must not invalidate that existing delivery.
        if not receipt_file.is_file():
            target.unlink(missing_ok=True)
        return {"download_error": "本机下载服务未启动，请从本机取用输出文件。"}


def frozen_fulfillment(manifest, document, receipt):
    marker = 'window.activeTour=mountTour(document.getElementById("tour"),'
    text = document.decode()
    if marker in text:
        tour = json.JSONDecoder().raw_decode(text.split(marker, 1)[1])[0]
    else:
        # A legacy photo page is still retrievable, but cannot become a panorama guide.
        tour = {"stations": [{"id": s["id"], "links": []} for s in manifest["stations"]]}
    return fulfillment(manifest, tour, receipt.get("grade", {}))


def pending_confirmation(manifest, result):
    action = manifest.get("next_action") or {}
    grade = result.get("grade", {})
    if (action.get("kind") != "confirm" or not manifest.get("visitor_route") or manifest.get("first_look")
            or grade.get("blocked") or grade.get("public_photos")):
        return None
    source, target = action.get("from_id"), action.get("to_id")
    specs = {s["id"]: s for s in manifest["stations"]}
    if not isinstance(source, str) or not isinstance(target, str) or source not in specs or target not in specs:
        return None
    direction = [e for e in result["edges"] if (e["from"], e["to"]) == (source, target)]
    if (not any(e["status"] in {"pending", "cut"} for e in direction)
            or any(e["status"] == "confirmed" for e in direction)):
        return None
    station = specs[source]
    if not any(isinstance(s, dict) and s.get("to_id") == target and isinstance(s.get("pending"), str)
               and s["pending"].strip() for s in (station.get("suggestions") or [])):
        return None
    step = (station.get("walk") or {}).get(target, {})
    if manifest.get("fixed_route") and not all(step.get(k) for k in ("see", "action", "arrive")):
        return None
    return {"from_id": source, "to_id": target}


def receipt_links(result, port):
    project, revision = result["project"], result["revision"]
    base = f"http://127.0.0.1:{port}"
    query = urlencode({"project": project, "revision": revision})
    result["task_url"] = f"{base}/?task=1&{query}"
    result["review_url"] = f"{base}/?preview=1&{query}"
    result["recover_url"] = f"{base}/?history=1&{urlencode(dict(project=project))}"
    result["preview_url"] = f"{base}/api/history/{project}/{revision}/tour.html"
    if result.get("route_card_sha256"):
        result["route_card_url"] = f"{base}/api/history/{project}/{revision}/route-card.jpg"
    grade = result.get("grade", {})
    manifest, _ = Project(project).read(revision)
    if "fulfillment" not in result:
        result["fulfillment"] = frozen_fulfillment(manifest, Path(result["output"]).read_bytes(), result)
    result["title"] = manifest.get("title") or "空间预览"
    result["preview_kind"] = ("实景草稿" if manifest.get("first_look") else
                              "分层预览" if grade.get("kind") == "venue-preview" else
                              "档案试走" if manifest.get("archive_notice") else
                              "路线预览" if "路线预览" in grade.get("label", "") else "预览")
    if not result["fulfillment"]["arrows"]:
        result["preview_kind"] = "看景草稿"
    elif not result["fulfillment"]["complete"]:
        result["preview_kind"] = "分层预览" if manifest.get("venue") else "局部实景试走"
    candidate = pending_confirmation(manifest, result)
    if candidate:
        result["confirm_url"] = result["task_url"] + "&" + urlencode(dict(confirm=1, **candidate))
    elif ((manifest.get("next_action") or {}).get("kind") != "confirm"
            and "venue" not in result and not grade.get("public_photos") and not grade.get("route_ready")
            and not grade.get("blocked") and (grade.get("kind") != "fixed" or grade.get("gap") is not None)):
        result["confirm_url"] = result["task_url"] + "&confirm=1"
    return result


def saved_result(key, revision, require_tour, *, require_panorama=False):
    manifest, base = Project(key).read(revision)
    result = json.loads((base / "receipt.json").read_text())
    result["manifest"] = str(base / "manifest.json")
    result["title"] = manifest.get("title") or "空间预览"
    grade = result.get("grade", {})
    if manifest.get("visitor_route") and "venue" not in manifest:
        result["walk_frames"] = Project.walk_frames(manifest)
    if (require_tour or require_panorama) and manifest.get("archive_notice"):
        raise ValueError("这版是档案试走，请从保存记录下载档案预览。")
    if require_tour and not require_panorama and not grade.get("route_ready"):
        raise ValueError("该保存版本没有通过成品导览门槛，请核对并导出新版")
    # Earlier receipts keep their original retrieval contract; strict requests still require current evidence.
    check_delivery(manifest if "panorama_ready" in grade else {}, grade, require_tour, require_panorama)
    require_tour = require_tour or require_panorama
    if require_tour and manifest.get("fixed_route"):
        issue = access_issue(route_info(manifest["fixed_route"]))
        if issue:
            raise ValueError(issue + " 请通过核实入口确认，不提供替代走法。")
    if require_tour and sha((base / "tour.html").read_bytes()) != result["sha256"]:
        raise ValueError("保存的网页与冻结回执不符")
    result["output"] = str(base / "tour.html")
    return result


def attachment_root():
    if SANDBOX:
        return ROOT / "media" / "outbound" / "space-preview"
    state = Path(os.environ.get("OPENCLAW_STATE_DIR") or "~/.openclaw").expanduser().resolve()
    return state / "media" / "space-preview"


def stage_attachment(path, content):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic(path, content)
    # The gateway resolves MEDIA paths using the current session's sandbox.
    return "./" + path.relative_to(ROOT).as_posix() if SANDBOX else str(path)


def tour_zip(document, title):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(title + ".html", document)
    return archive.getvalue()


# OpenClaw webchat uses a 5 MiB default for inline reply attachments.
def chat_zip(result, key, path, document, title):
    data = tour_zip(document, title)
    if SANDBOX and len(data) > 5 * 1024 * 1024:
        result["chat_zip_bytes"] = len(data)
        return
    result[key] = stage_attachment(path, data)


def chat_files(result, include_tour):
    output = Path(result["output"])
    folder = attachment_root() / result["project"] / result["revision"]
    if result.get("route_card_sha256"):
        card = output.with_name("route-card.jpg").read_bytes()
        if sha(card) != result["route_card_sha256"]:
            raise ValueError("实景路线图与冻结回执不符")
        name = "实景路线.jpg" if result["grade"]["route_ready"] else "实景路线草稿.jpg"
        result["route_attachment"] = stage_attachment(folder / name, card)
    document = output.read_bytes()
    if sha(document) != result["sha256"]:
        raise ValueError("交互导览与冻结回执不符")
    if include_tour:
        chat_zip(result, "tour_attachment", folder / "交互导览.zip", document, "交互导览")
    elif SANDBOX and result.get("grade", {}).get("kind") != "fixed":
        chat_zip(result, "preview_attachment", folder / "仅供试走-路线预览.zip", document, "仅供试走-路线预览")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("SPACE_PREVIEW_PORT", "18779")))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--project", help="save a version in this local project")
    parser.add_argument("--parent", help="revision being edited")
    parser.add_argument("--opening", nargs=2, metavar=("YAW", "PITCH"), type=float,
                        help="change only one saved opening view, in degrees")
    parser.add_argument("--opening-photo", type=int, metavar="INDEX", help="select a saved original for the fixed route opening")
    parser.add_argument("--opening-at", metavar="STATION", help="station ID for --opening (default: entry)")
    parser.add_argument("--venue-level", metavar="LEVEL", help="change the opening floor of a saved venue")
    parser.add_argument("--venue-recommended", nargs="*", metavar="STATION", help="replace the recommended places of a saved venue")
    parser.add_argument("--bind-spheres", action="store_true", help="bind anchors on initial direct sphere imports")
    parser.add_argument("--intake", type=Path, help="bind the current OpenClaw upload receipt to a saved version")
    parser.add_argument("--resume", action="store_true", help="list the saved revision and manifest path")
    downloads = parser.add_mutually_exclusive_group()
    downloads.add_argument("--chat-download", action="store_true", help="register the final tour download")
    downloads.add_argument("--preview-download", action="store_true", help="register an explicitly labelled preview download")
    parser.add_argument("--chat-files", action="store_true", help="stage a route card and, with --require-tour, the offline attachment")
    parser.add_argument("--chat-sample", action="store_true", help="stage the three-station sample as a chat attachment")
    parser.add_argument("--require-tour", action="store_true", help="export only a multi-station panorama with reciprocal scene arrows")
    parser.add_argument("--require-venue", action="store_true", help="require a venue with floor plans, a confirmed crossing and a branch")
    parser.add_argument("--require-walk", action="store_true", help="require a confirmed fixed walking route")
    parser.add_argument("--require-panorama", action="store_true",
                        help="require real scene arrows on the fixed route, a physical floor crossing in a venue, or three linear panorama stops")
    args = parser.parse_args()
    opening_edit = args.opening is not None or args.opening_photo is not None
    if args.opening and args.opening_photo is not None:
        parser.error("一次只指定一种开场画面")
    if sum((args.require_walk, args.require_tour, args.require_venue)) > 1:
        parser.error("请选择固定步行、线性全景或场馆总览成品门槛")
    require_tour = args.require_tour or args.require_walk or args.require_venue or args.require_panorama or args.chat_download

    venue_edit = args.venue_level is not None or args.venue_recommended is not None

    def check_mode(raw):
        is_venue = "venue" in raw
        if (is_venue and (args.require_tour or args.require_walk)) or (not is_venue and (args.require_venue or venue_edit)):
            raise ValueError("场馆总览用 --require-venue，线性全景往返用 --require-tour")
        fixed = "fixed_route" in raw
        if (fixed and args.require_tour) or (not fixed and args.require_walk):
            raise ValueError("固定步行路线用 --require-walk，全景往返用 --require-tour")
        if raw.get("archive_notice") and args.chat_download:
            raise ValueError("档案样本仅供本地试走，请使用该保存版预览")
        if fixed and args.preview_download:
            raise ValueError("固定路线草稿请使用冻结试走链接")

    if venue_edit and (not args.project or not args.parent or args.manifest or args.resume or
                       args.chat_sample or args.output or opening_edit):
        parser.error("场馆局部修改需要 --project 和 --parent")
    if args.opening_at and not opening_edit:
        parser.error("--opening-at 需与 --opening 合用")
    if opening_edit and (not args.project or not args.parent or
                         args.manifest or args.resume or args.chat_sample or args.output):
        parser.error("--opening 需要 --project 和 --parent，不与清单、恢复、样例或独立输出合用")
    if (args.bind_spheres or args.intake) and (not args.project or not args.manifest or args.resume or args.chat_sample):
        parser.error("--bind-spheres 和 --intake 需要项目清单")
    if args.chat_files and (not args.project or not (args.manifest or args.resume or opening_edit or venue_edit)):
        parser.error("聊天附件需要项目及保存版本")
    if args.resume and opening_edit:
        parser.error("恢复下载与开场修改请分别调用")
    if require_tour and (not args.project or not (args.manifest or args.resume or opening_edit or venue_edit)):
        parser.error("成品导览需要 --project 和 --manifest、--resume 或局部修改参数")
    if args.preview_download and (not args.project or not (args.manifest or args.resume or opening_edit or venue_edit)):
        parser.error("预览下载需要已保存的项目版本")
    try:
        if args.chat_sample:
            if args.project or args.manifest or args.output or args.parent:
                parser.error("样例与项目操作分开运行")
            document = visitor_sample()
            path = attachment_root() / "sample" / "试走样例-展厅.zip"
            result = {"sample_preview_url": f"http://127.0.0.1:{args.port}/sample/visitor-tour.html", "sha256": sha(document)}
            chat_zip(result, "sample_attachment", path, document, "试走样例-展厅")
        elif args.resume:
            if not args.project:
                parser.error("--resume requires --project")
            raw, base = Project(args.project).read(args.parent)
            check_mode(raw)
            result = saved_result(args.project, base.name, require_tour, require_panorama=args.require_panorama)
        elif venue_edit:
            project = Project(args.project)
            raw, _ = project.read(args.parent)
            check_mode(raw)
            if args.venue_level is not None:
                raw["venue"]["opening_level"] = args.venue_level
            if args.venue_recommended is not None:
                raw["venue"]["recommended"] = args.venue_recommended
            result = project.publish(raw, args.parent, prepare_station, portable, require_tour=require_tour,
                                     require_panorama=args.require_panorama)
            result["manifest"] = str(Path(result["output"]).with_name("manifest.json"))
        elif opening_edit:
            project = Project(args.project)
            raw, base = project.read(args.parent)
            check_mode(raw)
            previous = json.loads((base / "receipt.json").read_text())
            selected = args.opening_at or raw["entry"]
            station = next((s for s in raw["stations"] if s["id"] == selected), None)
            view = next((s for s in previous["stations"] if s["id"] == selected), None)
            if not (raw.get("visitor_route") or "venue" in raw) or not station or not view:
                raise ValueError("找不到要修改开场的站点")
            if args.opening_photo is not None:
                if not raw.get("fixed_route") or view.get("view"):
                    raise ValueError("只有使用原照的固定路线站点可以指定开场照片")
                station["opening_photo"] = args.opening_photo
            else:
                if not view.get("view") or not view.get("view_revision"):
                    raise ValueError("该站没有可设为开场的全景画面")
                yaw, pitch = args.opening
                hfov = (station.get("opening") or {}).get("hfov")
                station["opening"] = {"yaw": yaw, "pitch": pitch, "view_revision": view["view_revision"]}
                if hfov is not None:
                    station["opening"]["hfov"] = hfov
            result = project.publish(raw, args.parent, prepare_station, portable, require_tour=require_tour,
                                     require_panorama=args.require_panorama)
            result["manifest"] = str(Path(result["output"]).with_name("manifest.json"))
        elif args.manifest:
            if args.project:
                project = Project(args.project)
                raw = json.loads(args.manifest.read_text())
                if "amend" in raw:
                    raw = project.amend_manifest(raw, args.parent)
                check_mode(raw)
                if raw.get("project"):
                    if raw["project"] != args.project or raw.get("revision") != args.parent:
                        raise ValueError("清单必须属于当前项目和所指定的父版")
                    # Saved manifests refer to managed original paths; new files may be added relative to the draft.
                    for station in raw["stations"]:
                        station["files"] = [f if f.startswith("originals/") else project.import_asset(args.manifest.parent / f)
                                            for f in station["files"]]
                        if "alternates" in station:
                            station["alternates"] = [f if f.startswith("originals/") else project.import_asset(args.manifest.parent / f)
                                                     for f in station["alternates"]]
                        if station.get("capture") and not station["capture"].startswith("originals/"):
                            station["capture"] = project.import_asset(args.manifest.parent / station["capture"])
                else:
                    if args.parent:
                        raise ValueError("修改须从父版清单开始")
                    raw = project.import_manifest(args.manifest)
                project.import_plans(raw, args.manifest.parent, managed=bool(raw.get("project")))
                if args.intake:
                    from prepare_photos import verify_originals

                    path = args.intake.resolve()
                    relative = path.relative_to(ROOT / "media" / "inbound")
                    if not re.fullmatch(r"[a-f0-9-]{36}/[a-f0-9-]{36}/receipt\.json", relative.as_posix()):
                        raise ValueError("请选择本轮原件回执")
                    intake = json.loads(path.read_text())
                    if (intake.get("session_id"), intake.get("run")) != relative.parts[:2] or not re.fullmatch(
                            r"agent:space-preview:[A-Za-z0-9:_-]+", intake.get("session", "")):
                        raise ValueError("收件回执与当前会话不匹配")
                    workflow = (project.read(args.parent)[0].get("workflow") if args.parent else None) or {
                        "session": intake["session"], "batches": []}
                    if workflow["session"] != intake["session"]:
                        raise ValueError("补图回执必须属于原会话")
                    if not any(batch["run"] == intake["run"] for batch in workflow["batches"]):
                        verify_originals(path, intake)
                        workflow["batches"].append({key: intake[key] for key in ("run", "files")})
                    raw["workflow"] = workflow
                if args.output and args.output.exists():
                    raise ValueError("外部导出文件已存在")
                result = project.publish(raw, args.parent, prepare_station, portable,
                                         require_tour=require_tour, bind_spheres=args.bind_spheres,
                                         require_panorama=args.require_panorama)
                result["manifest"] = str(Path(result["output"]).with_name("manifest.json"))
                if args.output:
                    args.output.write_bytes(Path(result["output"]).read_bytes())
            else:
                if not args.output or args.parent:
                    parser.error("legacy --manifest requires --output; --parent requires --project")
                tour = cli_tour(args.manifest)
                args.output.write_text(portable(tour), encoding="utf-8")
                result = {"output": str(args.output.resolve()), "release": RELEASE,
                          "stations": [summary(s) for s in tour["stations"]]}
        else:
            with WorkbenchServer(("127.0.0.1", args.port), Handler) as server:
                print(f"http://127.0.0.1:{server.server_port}", flush=True)
                server.serve_forever()
            raise SystemExit
        if args.project:
            receipt_links(result, args.port)
        if args.chat_download or args.preview_download:
            kind = result["preview_kind"] if args.preview_download else ""
            result.update(register_download(Path(result["output"]), args.port, result["title"], kind))
        if args.chat_files:
            chat_files(result, require_tour)
        print(json.dumps(result, ensure_ascii=False))
    except FileNotFoundError as e:
        parser.exit(2, "空间预览：找不到任务版本或原片。继续旧任务请核对项目及保存版；新任务请核对本轮附件。\n")
    except (ValueError, TypeError, AttributeError, KeyError, OSError) as e:
        parser.exit(2, f"空间预览：{e}\n")
