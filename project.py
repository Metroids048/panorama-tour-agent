"""Local project storage and versioned route assembly."""
import base64
import fcntl
import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
import uuid
from copy import deepcopy
from datetime import date
from pathlib import Path

import cv2
import numpy
import PIL
from PIL import Image
from delivery import bind_request, check_delivery, fulfillment, protect_routes, quality, requested_route_gap
from fixed_route import grade_route, https_url, public_photos, route_info, sign_crop, words
from presentation import enhance_sphere
from route_card import render as render_route_card
import venue
import retention

ROOT = Path(__file__).resolve().parent
PROJECTS = ROOT / "projects"
MAX_FILE = 32 * 1024 * 1024
MAX_STATION_BYTES = 35 * 1024 * 1024
MAX_STATIONS = 32
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}")
REV = re.compile(r"[0-9a-f]{32}")
SOURCE = re.compile(r"originals/[0-9a-f]{64}/[^/\\]+")
ENGINE = ("processing.py", "engine.py", "recovery.py", "align.py", "guided.py")
EXPORT = ("viewer.js", "viewer.css", "pannellum.js", "pannellum.css", "COPYING")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()


def digest(names):
    return sha(b"".join((ROOT / name).read_bytes() for name in names))


PIPELINE = sha(encoded([digest(ENGINE), cv2.__version__, numpy.__version__, PIL.__version__]))
# The workbench was split from the unchanged renderer. Keep existing cached
# textures and their bound arrow coordinates for this exact rendering build.
if PIPELINE == "2f9fb5e77770f5c0d3588608483ace38ab9fb60fc2fb5fae8cc4ba1132cb91fc":
    PIPELINE = "96a2209aca14acab0975bba7594850e7a9a95ba056d74c7c437b165c13290aba"
# The optional guided resolution and seam settings are keyed by their capture record.
if PIPELINE == "eb021e766c3a25414571a5d59bd69fa5100c1543d4309fb0cac66719dbaa89fb":
    PIPELINE = "d157a31a37a041ecd67b733b1d6cd5e1d93d4ed661f022179003c64d58ee132f"
# These builds add imports for previously rejected near-2:1 and MPO spheres.
if PIPELINE in {
    "16400439fa2fb300d8b47d20f96a052689530a2876f02ff6339e9505d5d62c4e",
    "9486175454178a2033c3340dbd93e45af338bb8bd35d5f21fc0809eba98b24ff",
}:
    PIPELINE = "6ac9f031996a295122623619c841431ff5969d1072120426147b2de0c8b893ac"
RELEASE = digest(ENGINE + EXPORT + ("app.py", "project.py", "retention.py", "presentation.py", "history.py", "delivery.py", "fixed_route.py", "venue.py", "route_card.py", "history.js", "editor.js", "index.html", "workbench.css"))


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(data)
    try:
        Path(tmp.name).replace(path)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def label(value, default):
    if not isinstance(value, str):
        raise ValueError("标题和点位名应为文字")
    return value.strip()[:70] or default


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("项目或站点标识无效，请用字母、数字、横线或下划线")
    return value


def summary(s):
    result = {k: s[k] for k in ("id", "name", "kind", "reason", "matched", "followup", "capture",
                              "geometry", "view_revision", "reused", "inputs") if k in s}
    matched = s.get("matched", [])
    result["used_files"] = [s["originals"][i]["name"] for i in matched]
    if s["panorama"]:
        inputs = len(s.get("inputs", s["originals"]))
        result["unused_files"] = [photo["name"] for i, photo in enumerate(s["originals"][:inputs])
                                  if i not in matched]
        result["view"] = {k: v for k, v in s["panorama"].items()
                          if k in ("haov", "vaov", "vOffset", "width", "height", "method")}
        if "width" not in result["view"] or "height" not in result["view"]:
            data = base64.b64decode(s["panorama"]["image"].split(",", 1)[1])
            with Image.open(io.BytesIO(data)) as texture:
                result["view"]["width"], result["view"]["height"] = texture.size
    return result


class Project:
    def __init__(self, key):
        self.key = identifier(key)
        self.root = PROJECTS / self.key

    def version_path(self, revision):
        if not isinstance(revision, str) or not REV.fullmatch(revision):
            raise ValueError("导出版本标识无效")
        return self.root / "versions" / revision

    def read(self, revision=None):
        if not revision:
            revision = (self.root / "latest").read_text().strip()
        base = self.version_path(revision)
        return json.loads((base / "manifest.json").read_text()), base

    def original(self, name, data):
        if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise ValueError("原片文件名无效")
        if len(data) > MAX_FILE:
            raise ValueError(f"{name}：每张原图最多 32 MB")
        path = f"originals/{sha(data)}/{name}"
        dest = self.root / path
        if not dest.exists():
            atomic(dest, data)
        return path

    def asset(self, path):
        if not isinstance(path, str) or not SOURCE.fullmatch(path) or Path(path).name in {".", ".."}:
            raise ValueError("项目原片标识无效")
        full = self.root / path
        if full.stat().st_size > MAX_FILE:
            raise ValueError(f"{full.name}：每张原图最多 32 MB")
        data = full.read_bytes()
        if sha(data) != full.parent.name:
            raise ValueError(f"{full.name}：项目原片已变化")
        return data

    def import_asset(self, path):
        if path.stat().st_size > MAX_FILE:
            raise ValueError(f"{path.name}：每张原图最多 32 MB")
        return self.original(path.name, path.read_bytes())

    def import_manifest(self, manifest):
        data = json.loads(manifest.read_text())
        for s in data["stations"]:
            s["files"] = [self.import_asset(manifest.parent / f) for f in s["files"]]
            if "alternates" in s:
                s["alternates"] = [self.import_asset(manifest.parent / f) for f in s["alternates"]]
            if s.get("capture"):
                s["capture"] = self.import_asset(manifest.parent / s["capture"])
            if s.get("source_archive"):
                s["source_archive"] = self.import_asset(manifest.parent / s["source_archive"])
        return data

    @staticmethod
    def walk_frames(data):
        """Input bindings for adjacent directions; never a passage confirmation."""
        raw = data['stations']
        return {station['id']: {raw[j]['id']: sha(encoded([
                    station['files'], station.get('alternates', []),
                    raw[j]['files'], raw[j].get('alternates', [])]))
                for j in (i-1, i+1) if 0 <= j < len(raw)}
                for i, station in enumerate(raw)}

    def amend_manifest(self, envelope, parent):
        """Patch metadata and existing anchors without replacing the saved route."""
        if (set(envelope) != {"project", "revision", "amend"} or not parent or
                envelope["project"] != self.key or envelope["revision"] != parent):
            raise ValueError("局部清单必须属于当前项目和确切父版，不能混入完整清单")
        changes = envelope["amend"]
        allowed = {"title", "attribution", "historical_visit", "archive_notice", "fixed_route", "destination", "stations", "navigation_map"}
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise ValueError("局部清单只可修改范围说明、固定路线资料及原站标签、走法和既有落点")
        data = deepcopy(self.read(parent)[0])
        for key, value in changes.items():
            if key == "navigation_map":
                if (not isinstance(value, dict) or set(value) - {"name", "plan", "source"} or
                        not isinstance(data.get(key), dict)):
                    raise ValueError("局部地图只可改既有底图和说明，点位坐标不可变")
                data[key].update(value)
            elif key != "stations":
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
        updates = changes.get("stations", [])
        if not isinstance(updates, list):
            raise ValueError("局部站点清单应为数组")
        stations = {s["id"]: s for s in data["stations"]}
        seen = set()
        for update in updates:
            if (not isinstance(update, dict) or set(update) - {"id", "name", "links", "walk", "photo_sources"} or
                    update.get("id") not in stations or update["id"] in seen):
                raise ValueError("局部站点须使用不重复的原ID，只可改标签、走法署名及既有箭头落点")
            seen.add(update["id"])
            station = stations[update["id"]]
            if "name" in update and (not isinstance(update["name"], str) or not update["name"].strip()):
                raise ValueError("站名须为非空文字")
            if "links" in update:
                patches = update["links"]
                if not isinstance(patches, list) or not patches:
                    raise ValueError("局部箭头须指定既有方向，不能用空数组删除")
                links = {link["to_id"]: link for link in station.get("links", [])}
                touched = set()
                editable = {"yaw", "pitch", "cue", "warning", "confirmed_by", "evidence", "route_proof"}
                for item in patches:
                    target = item.get("to_id") if isinstance(item, dict) else None
                    if target not in links or target in touched:
                        raise ValueError("局部箭头须使用不重复的既有目标，不能增删连接")
                    link = links[target]
                    if any(key not in editable and (key not in link or value != link[key])
                           for key, value in item.items()):
                        raise ValueError("局部箭头只可改角度、提示和复核依据，原图绑定不可变")
                    touched.add(target)
                    link.update(item)
            station.update({key: value for key, value in update.items() if key != "links"})
        return data

    def import_plans(self, data, folder, managed):
        navigation = data.get("navigation_map")
        if navigation and not isinstance(navigation, dict):
            raise ValueError("地图配置应为对象")
        if navigation and navigation.get("plan") and not (managed and SOURCE.fullmatch(navigation["plan"])):
            navigation["plan"] = self.import_asset(folder / navigation["plan"])
        if "venue" in data:
            for level in data["venue"]["levels"]:
                plan = level.get("plan")
                if plan and not (managed and SOURCE.fullmatch(plan)):
                    level["plan"] = self.import_asset(folder / plan)

    def station(self, station, process, prior=None):
        paths = station["files"]
        if not isinstance(paths, list) or not 1 <= len(paths) <= (96 if station.get("capture") else 24):
            raise ValueError("每站照片数量无效")
        files = [(Path(p).name, self.asset(p)) for p in paths]
        if sum(len(b) for _, b in files) > MAX_STATION_BYTES:
            raise ValueError("每站原始文件合计最多 35 MB")
        record = station.get("capture")
        spherical = station.get("spherical") is True
        key = sha(encoded([PIPELINE, paths, record, spherical]))
        cache = self.root / "views" / (key + ".json")
        reused = cache.exists()
        if reused:
            result = json.loads(cache.read_text())
        else:
            capture = json.loads(self.asset(record)) if record else None
            result = process("", files, spherical, capture)
            atomic(cache, encoded(result))
        # Bind anchors to the prepared frame; an exact 2:1 texture at a
        # different resolution still has the same pitch and yaw coordinates.
        visible = station.get("use_originals") is True
        pano_frame = sha(encoded([key, result["panorama"], result["originals"]]))
        photo_frame = sha(encoded([key, None, result["originals"]]))
        if (prior and not visible and result["panorama"] and
                all(station.get(k) == prior.get(k) for k in ("files", "spherical", "capture", "use_originals"))):
            frames = {a.get("view_revision") for a in [prior.get("opening") or {}, *prior.get("links", [])]}
            frames.discard(None)
            if frames and pano_frame not in frames:
                # A pipeline/cache-key update may produce byte-identical pixels.
                # Retain a saved frame only when both its texture and originals
                # match exactly; changed pixels still invalidate old anchors.
                for candidate in (self.root / "views").glob("*.json"):
                    cached = json.loads(candidate.read_text())
                    if (cached.get("panorama") == result["panorama"] and cached.get("originals") == result["originals"]):
                        old_frame = sha(encoded([candidate.stem, cached["panorama"], cached["originals"]]))
                        if old_frame in frames:
                            pano_frame = old_frame
                            break
        if visible and result["panorama"]:
            result.update(panorama=None, kind="photos", matched=[], reason="本点位展示原图。")
        elif not record:
            result = enhance_sphere(result, files, spherical)
        return dict(result, view_revision=photo_frame if visible else pano_frame, photo_revision=photo_frame,
                    reused=reused, inputs=paths)

    @staticmethod
    def valid_surface_review(review):
        if (not isinstance(review, dict)
                or review.get("kind") != "walkable_surface"
                or not isinstance(review.get("material"), str)
                or not review["material"].strip()):
            return False
        left, right = review.get("left_pct"), review.get("right_pct")
        return (type(left) in (int, float) and type(right) in (int, float)
                and 0 <= left <= 45 and 55 <= right <= 100)

    def valid_route_proof(self, workflow, station, item, edge, *, require_surface_review=True):
        source = self.root / station["files"][0]
        ticket = edge.get("route_proof")
        if not isinstance(ticket, str):
            return False
        run_roots = [ROOT / "tasks" / workflow["session"].split(":")[-1] / "uploads" / b["run"]
                     for b in workflow["batches"]]
        try:
            path = Path(ticket).resolve(strict=True)
            if not any(path.is_relative_to(root) for root in run_roots):
                return False
            payload = path.read_bytes()
            if path.name != f"route-proof-{sha(payload)}.json":
                return False
            proof = json.loads(payload)
            coordinates = proof["points"].get(edge["to_id"])
            return (proof["source_id"] == station["id"] and proof["view_revision"] == item["view_revision"]
                    and proof["source_sha256"] == source.parent.name
                    and coordinates == [edge["yaw"], edge["pitch"]]
                    and (not require_surface_review or not proof.get("surface_review")
                         or self.valid_surface_review(edge.get("surface_review")))
                    and proof["image_sha256"] == sha(path.with_suffix(".jpg").read_bytes()))
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            return False

    def assemble(self, data, process, bind_spheres=False, bind_walk=False, previous=None, previous_edges=()):
        raw = data["stations"]
        is_venue = "venue" in data
        if not isinstance(raw, list) or not (0 if is_venue else 1) <= len(raw) <= MAX_STATIONS:
            raise ValueError("场馆总览最多 32 站" if is_venue else "导览应有 1–32 站")
        ids = [identifier(s["id"]) for s in raw]
        if len(set(ids)) != len(ids):
            raise ValueError("站点标识不能重复")
        entry = data.get("entry", ids[0] if ids else None)
        if (ids and entry not in ids) or (not ids and entry is not None):
            raise ValueError("入口必须是路线内的一站")
        tour = {"title": label(data.get("title", ""), "空间预览"), "entry": ids.index(entry) if ids else None, "stations": []}
        if data.get("navigation_map"):
            tour["navigation_map"] = venue.navigation_map(data["navigation_map"], ids, self.asset, raw)
        guided = data.get("visitor_route") is True
        if is_venue:
            if guided:
                raise ValueError("场馆总览与线性认路是两种独立的导览模式")
            tour["venue"], connections = venue.overview(data["venue"], raw, self.asset, identifier)
        else:
            connections = {}
        if guided:
            tour["visitor_route"] = True
        fixed = data.get("fixed_route")
        walk_frames = self.walk_frames(data) if fixed is not None else {}
        if "destination" in data:
            destination = data["destination"]
            if not guided or fixed is not None or destination not in ids:
                raise ValueError("目的地必须是线性导览内的一站")
            tour["destination"] = ids.index(destination)
        if fixed is not None:
            sphere_route = all(s.get("spherical") is True and not s.get("use_originals") for s in raw)
            if not guided or not isinstance(fixed, dict) or entry != ids[0] or (len(ids) > 5 and not sphere_route):
                raise ValueError("固定路线需要 visitor_route、第一站为入口；普通照片最多 5 个决策点，完整全景可保留至 32 站")
            info = route_info(fixed)
            survey = fixed.get("survey") or {}
            info["survey_record"] = (not info.get("public_photos") and survey.get("date") == info["checked_on"]
                                     and all(words(survey, k) for k in ("source", "permission", "continuity")))
            tour["fixed_route"] = info
        if isinstance(data.get("attribution"), str):
            tour["attribution"] = data["attribution"]
        if "historical_visit" in data:
            scope = data["historical_visit"]
            try:
                captured = date.fromisoformat(scope["captured_on"])
                source = words(scope, "source", 240)
                if captured > date.today() or not source:
                    raise ValueError()
            except (TypeError, KeyError, ValueError):
                raise ValueError("历史参观需要真实的非未来拍摄日期与来源说明") from None
            tour["historical_visit"] = {"captured_on": captured.isoformat(), "source": source}
        if isinstance(data.get("archive_notice"), str) and data["archive_notice"].strip():
            tour["archive_notice"] = data["archive_notice"].strip()[:120]
        forward_only = guided and fixed is None and bool(tour.get("archive_notice")) and data.get("forward_only") is True
        if forward_only:
            tour["forward_only"] = True
        route_pairs = None
        if "route_connections" in data:
            declared = data["route_connections"]
            if (not guided or is_venue or fixed is not None or data.get("forward_only")
                    or not isinstance(declared, list) or not 1 <= len(declared) <= MAX_STATIONS * (MAX_STATIONS - 1)):
                raise ValueError("分支通道需用于可往返实景导览，并逐方向声明连接")
            route_pairs = set()
            for connection in declared:
                if not isinstance(connection, dict):
                    raise ValueError("通道连接需要 from_id 与 to_id")
                pair = connection.get("from_id"), connection.get("to_id")
                if (pair[0] not in ids or pair[1] not in ids or pair[0] == pair[1] or pair in route_pairs):
                    raise ValueError("通道必须连接不同的已知站点，且不得重复")
                route_pairs.add(pair)
            tour["route_connections"] = [{"from": ids.index(a), "to": ids.index(b)} for a, b in sorted(route_pairs)]
        if data.get("first_look") is True:
            tour["first_look"] = True
        edges = []
        prior = {s["id"]: s for s in previous["stations"]} if previous else {}
        carried = {(edge["from"], edge["to"]) for edge in previous_edges if edge["status"] == "confirmed"}
        workflow = data.get("workflow", {})
        chat = workflow.get("session", "").startswith("agent:space-preview:openagent:")
        added = set(ids) - prior.keys()
        for i, station in enumerate(raw):
            item = self.station(station, process, prior.get(ids[i]))
            item.update(id=ids[i], name=label(station.get("name", ""), f"点位 {i+1}"), links=[])
            existing = prior.get(ids[i])
            same_input = existing and all(station.get(key) == existing.get(key)
                                          for key in ("files", "spherical", "capture", "use_originals"))
            added_links = [edge for edge in station.get("links", [])
                           if edge.get("to_id") in added or previous and previous.get("first_look")]
            if bind_spheres and (not existing or added_links and same_input):
                pano = item["panorama"]
                if (not (guided or is_venue) or not station.get("spherical") or len(station["files"]) != 1
                        or station.get("capture") or not pano or (pano["haov"], pano["vaov"]) != (360, 180)):
                    raise ValueError(f'{item["name"]}：只有未裁切的单张实拍球面可在导入时绑定')
                opening = station.get("opening")
                if opening and not existing:
                    opening.setdefault("view_revision", item["view_revision"])
                for edge in added_links if existing else station.get("links", []):
                    if "yaw" in edge and not edge.get("cut"):
                        edge.setdefault("view_revision", item["view_revision"])
            if guided or is_venue:
                item["landmark"] = label(station.get("landmark", ""), item["name"])
                opening = station.get("opening") or {}
                pano = item["panorama"]
                # The first arrangement may compose the same sphere already shown in first look.
                # This only binds the opening, never a direction or a replacement image.
                if (opening and same_input and previous.get("first_look") and not data.get("first_look")
                        and station.get("spherical") and pano and (pano["haov"], pano["vaov"]) == (360, 180)):
                    opening.setdefault("view_revision", item["view_revision"])
                if pano and opening.get("view_revision") == item["view_revision"]:
                    yaw, pitch = opening.get("yaw"), opening.get("pitch")
                    if (type(yaw) not in (int, float) or type(pitch) not in (int, float)
                            or not math.isfinite(yaw) or not math.isfinite(pitch)
                            or abs(yaw) > pano["haov"] / 2
                            or abs(pitch - pano["vOffset"]) > pano["vaov"] / 2):
                        raise ValueError(f'{item["name"]}：开场朝向超出画面')
                    item["opening"] = {"yaw": yaw, "pitch": pitch}
                    if "hfov" in opening:
                        hfov = opening["hfov"]
                        if type(hfov) not in (int, float) or not math.isfinite(hfov) or not 0 < hfov <= 90:
                            raise ValueError(f'{item["name"]}：开场缩放超出画面')
                        item["opening"]["hfov"] = hfov
                alternates = station.get("alternates", [])
                if not isinstance(alternates, list) or len(alternates) > 3:
                    raise ValueError("每站最多保留 3 张同站候选")
                if alternates:
                    detail = self.station({"files": alternates, "use_originals": True}, process)
                    item["originals"] = [*item["originals"], *detail["originals"]]
            tour["stations"].append(item)
            if fixed is not None:
                item["opening_photo"] = station.get("opening_photo", 0)
                if type(item["opening_photo"]) is not int or not 0 <= item["opening_photo"] < len(item["originals"]):
                    raise ValueError(f'{item["name"]}：开场照片编号超出范围')
                if info.get("public_photos"):
                    item["photo_sources"] = public_photos(station.get("photo_sources"), len(item["originals"]))
                    if "sign_crop" in station:
                        item["sign_crop"] = sign_crop(station["sign_crop"],
                                                      [*station["files"], *alternates], item["originals"])
                item["landmark"] = label(station.get("landmark", ""), item["name"])
                item["walk"], item["walk_frames"] = {}, {}
                declared = station.get("walk", {})
                if not isinstance(declared, dict) or set(declared) - {ids[j] for j in (i-1, i+1) if 0 <= j < len(ids)}:
                    raise ValueError("步行方向必须指向相邻站")
                for j in (i-1, i+1):
                    if not 0 <= j < len(ids):
                        continue
                    target = ids[j]
                    direction = declared.get(target, {})
                    frame = walk_frames[ids[i]][target]
                    item["walk_frames"][target] = frame
                    if bind_walk and words(direction, "evidence") and words(direction, "confirmed_by"):
                        direction.setdefault("frame", frame)
                    step = {k: words(direction, k) for k in ("see", "action", "arrive", "pending")}
                    step["confirmed"] = (direction.get("frame") == frame and
                                         all(step[k] for k in ("see", "action", "arrive")) and
                                         all(words(direction, k) for k in ("evidence", "confirmed_by")) and
                                         (not info.get("public_photos") or bool(https_url(direction, "evidence_url"))))
                    if step["confirmed"] and info.get("public_photos") and "orientation" in direction:
                        orientation = direction["orientation"]
                        fields = ("road", "heading", "distance", "left")
                        if not isinstance(orientation, dict) or not all(words(orientation, key, 24) for key in fields):
                            raise ValueError("方向示意需提供已核对的道路、行进方向、约略距离和左手地标")
                        step["orientation"] = {key: words(orientation, key, 24) for key in fields}
                    if not step["confirmed"]:
                        step.pop("action")
                        step.pop("arrive")
                        if not step["pending"]:
                            step["pending"] = f'请确认「{item["name"]}」通往「{label(raw[j].get("name", ""), f"点位 {j+1}")}」的走法。'
                    item["walk"][j] = step
            used = set()
            for edge in station.get("links", []):
                target = edge["to_id"]
                if target not in ids or target == ids[i]:
                    raise ValueError("方向边必须指向另一已知站点")
                to = ids.index(target)
                if forward_only and to < i:
                    continue
                cut = edge.get("cut") is True
                identity = (target, "cut" if cut else edge.get("photo"))
                if identity in used:
                    raise ValueError("同一画面到同一站只能放一个箭头")
                used.add(identity)
                status = "confirmed"
                connection = connections.get((ids[i], target))
                if is_venue and (not connection or cut or "photo" in edge):
                    raise ValueError("场馆箭头需要已声明的方向连接和源站全景")
                if is_venue and connection.get("pending"):
                    status = "pending"
                elif route_pairs is not None and (ids[i], target) not in route_pairs:
                    status = "undeclared"
                elif not is_venue and route_pairs is None and abs(to - i) != 1:
                    status = "not-adjacent"
                elif cut:
                    status = "cut"
                elif not all(isinstance(edge.get(k), str) and edge[k].strip() for k in ("confirmed_by", "evidence")):
                    status = "unconfirmed"
                elif edge.get("view_revision") != item["view_revision"]:
                    status = "stale-view"
                elif is_venue and not item["panorama"]:
                    status = "no-panorama"
                elif (chat and not cut and "yaw" in edge and station.get("spherical")
                      and len(station["files"]) == 1 and not station.get("capture")
                      and item["panorama"] and (item["panorama"]["haov"], item["panorama"]["vaov"]) == (360, 180)):
                    inherited = (same_input and (ids[i], target) in carried
                                 and not edge.get("route_proof") and edge in existing.get("links", []))
                    if not (inherited or self.valid_route_proof(workflow, station, item, edge)):
                        status = "missing-route-proof"
                if fixed is not None and (cut or not item["walk"].get(to, {}).get("confirmed")):
                    continue
                if fixed is not None and info.get("public_photos") and "photo" in edge:
                    continue
                if status == "cut":
                    item["links"].append({"to": to, "cut": True})
                elif status == "confirmed":
                    coord = ("photo", "x", "y") if "photo" in edge else ("yaw", "pitch")
                    link = {"to": to, **{k: edge[k] for k in coord}}
                    if (guided or is_venue) and edge.get("cue"):
                        link["cue"] = label(edge["cue"], "")
                    if (guided or is_venue) and edge.get("warning"):
                        link["warning"] = label(edge["warning"], "")
                    item["links"].append(link)
                edges.append({"from": ids[i], "to": target, "status": status,
                              **({"photo_only": True} if not cut and "photo" in edge else {})})
            neighbors = ([ids.index(b) for a, b in sorted(route_pairs) if a == ids[i]] if route_pairs is not None
                         else () if is_venue else (i + 1,) if forward_only else (i - 1, i + 1))
            for j in neighbors:
                if 0 <= j < len(ids) and not any(e["from"] == ids[i] and e["to"] == ids[j] and
                                               e["status"] in {"confirmed", "cut"} for e in edges):
                    edges.append({"from": ids[i], "to": ids[j], "status": "pending"})
            if guided:
                unresolved = {ids[j]: j for j in neighbors if 0 <= j < len(ids)
                              and not any(link["to"] == j and "yaw" in link for link in item["links"])}
                pending = {}
                suggestions = station.get("suggestions")
                for suggestion in suggestions if isinstance(suggestions, list) else []:
                    if not isinstance(suggestion, dict):
                        continue
                    target, note = suggestion.get("to_id"), suggestion.get("pending")
                    if isinstance(target, str) and target in unresolved and isinstance(note, str) and note.strip():
                        pending.setdefault(unresolved[target], label(note, ""))
                if pending:
                    item["pending"] = pending
        if is_venue:
            statuses = {(e["from"], e["to"]): e["status"] for e in edges}
            for pair, connection in connections.items():
                if statuses.get(pair) != "confirmed":
                    connection.setdefault("pending", "方向依据或源站画面锚点待核对")
                    if pair not in statuses:
                        edges.append({"from": pair[0], "to": pair[1], "status": "pending"})
        return tour, edges

    def publish(self, data, parent, process, portable, require_tour=False, bind_spheres=False, *, require_panorama=False):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".publish.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            latest = self.root / "latest"
            if latest.exists() and latest.read_text().strip() != parent:
                raise ValueError("这个任务已有更新，请打开最新版本后再保存")
            receipt = self._publish(data, parent, process, portable, require_tour, bind_spheres, require_panorama)
            # Only after the frozen export and latest pointer have committed.
            # Reuse this lock; housekeeping must never turn a saved tour into a failed save.
            try:
                retention.prune_locked(self.root, ROOT / "openclaw-workspace" / ".downloads")
            except (OSError, ValueError, KeyError, TypeError) as error:
                receipt["retention_warning"] = type(error).__name__
            return receipt

    def _publish(self, data, parent, process, portable, require_tour, bind_spheres, require_panorama):
        previous = None
        previous_edges = []
        if parent:
            previous, previous_base = self.read(parent)
            previous_edges = json.loads((previous_base / "receipt.json").read_text())["edges"]
            if ("venue" in previous) != ("venue" in data):
                session = (previous.get("workflow") or {}).get("session", "")
                if not (session.startswith("agent:space-preview:openagent:") and previous.get("first_look")
                        and not data.get("first_look") and not any(e["status"] == "confirmed" for e in previous_edges)
                        and data.get("request", previous.get("request")) == previous.get("request")):
                    raise ValueError("同一任务不能切换场馆总览模式")
                if previous.get("request"):
                    previous["request"] = {**previous["request"], "mode": "venue" if "venue" in data else "route"}
                    data["request"] = previous["request"]
            workflow = previous.get("workflow")
            if workflow:
                if data.get("workflow", workflow).get("session") != workflow["session"]:
                    raise ValueError("补图回执必须属于原会话")
                data.setdefault("workflow", workflow)
        elif (self.root / "latest").exists():
            raise ValueError("已有导出，修改时请指定父版")
        bind_request(data, previous)
        tour, edges = self.assemble(data, process, bind_spheres, bind_walk=not parent, previous=previous,
                                    previous_edges=previous_edges)
        protect_routes(previous, data, previous_edges, edges)
        route_changes = data.pop("route_changes", [])
        stations = [summary(s) for s in tour["stations"]]
        grade = (venue.quality(tour["venue"], stations, tour["entry"]) if "venue" in tour
                 else grade_route(tour) if "fixed_route" in tour else
                 quality(stations, edges, reciprocal=not tour.get("forward_only"),
                         required=[(c["from_id"], c["to_id"]) for c in data["route_connections"]]
                         if "route_connections" in data else None, entry=data.get("entry")))
        if "fixed_route" in tour:
            scene_grade = quality(stations, edges, reciprocal=grade["reverse"], minimum=3)
            grade["panorama_ready"] = grade["route_ready"] and scene_grade["route_ready"]
            grade["panorama_reasons"] = grade["reasons"] + scene_grade["reasons"]
            grade["detail_limited"] = scene_grade["detail_limited"]
            if grade["panorama_ready"]:
                grade["label"] = "固定路线 · 实景全景"
        elif "venue" not in tour:
            grade["panorama_ready"] = grade["route_ready"] and len(stations) >= 3
            grade["panorama_reasons"] = grade["reasons"] + (["至少需要 3 个实景点位。"] if len(stations) < 3 else [])
        if grade.get("blocked"):
            for station in tour["stations"]:
                station["links"] = []
                for step in station["walk"].values():
                    step["confirmed"] = False
                    step.pop("action", None)
                    step.pop("arrive", None)
        if (tour.get("visitor_route") and grade["kind"] == "limited"
                and all(s.get("view") for s in stations)):
            grade["label"] = "路线预览"
        if (data.get("next_action") or {}).get("kind") == "photo":
            missing = label(data["next_action"].get("title"), "还需补拍一张照片")
            grade.update(route_ready=False, panorama_ready=False, label="待补图 · 路线预览",
                         reasons=[missing, *grade["reasons"]],
                         panorama_reasons=[missing, *grade.get("panorama_reasons", [])])
        if tour.get("first_look"):
            note = "请先核对照片、开场和实地通路。"
            grade.update(route_ready=False, panorama_ready=False, label="实景草稿",
                         reasons=[note], panorama_reasons=[note])
        if missing := [f'{edge["from"]} → {edge["to"]}' for edge in edges if edge["status"] == "missing-route-proof"]:
            note = "以下实景方向缺少与源图、画面版本和角度一致的核点回执：" + "、".join(missing)
            grade.update(route_ready=False, panorama_ready=False, label="路线预览")
            grade["reasons"].append(note)
            grade["panorama_reasons"].append(note)
        if note := requested_route_gap(tour, data.get("required_route")):
            grade.update(route_ready=False, panorama_ready=False, label="路线预览")
            grade["reasons"].append(note)
            grade["panorama_reasons"].append(note)
        check_delivery(data, grade, require_tour, require_panorama)
        outcome = fulfillment(data, tour, grade)
        public = dict(tour, grade=grade, fulfillment=outcome, stations=[{k: v for k, v in s.items() if k not in
                                      {"inputs", "reused", "photo_revision", "view_revision", "walk_frames"}}
                                     for s in tour["stations"]])
        for station, facts in zip(public["stations"], stations):
            station["preview"] = venue.scene_preview(station)
            exact = station["originals"][0].get("panorama_source", False)
            if "venue" in tour or exact:
                station["panorama"] = venue.portable_sphere(
                    station["panorama"], facts.get("view", {}).get("width", 0), preserve_source=exact)
        document = portable(public).encode()
        revision = uuid.uuid4().hex
        final = self.version_path(revision)
        final.parent.mkdir(parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(dir=final.parent, prefix=".staging-"))
        try:
            receipt = {"project": self.key, "revision": revision, "parent": parent, "release": RELEASE,
                       "entry": tour["stations"][tour["entry"]]["id"] if stations else None,
                       "order": [s["id"] for s in tour["stations"]],
                       "output": str(final / "tour.html"), "sha256": sha(document),
                       "stations": stations, "edges": edges, "grade": grade, "fulfillment": outcome,
                       "route_changes": route_changes}
            if "fixed_route" in tour:
                receipt["walk_frames"] = {s["id"]: s["walk_frames"] for s in tour["stations"]}
            normalized = dict(data, entry=receipt["entry"], project=self.key, parent=parent, revision=revision)
            if "venue" in tour:
                receipt["venue"] = {**{k: tour["venue"][k] for k in ("coverage", "opening_level", "connections", "require_cross_floor")},
                                    "levels": [{k: level[k] for k in ("id", "name", "floor") if k in level}
                                               for level in tour["venue"]["levels"]],
                                    "recommended": data["venue"].get("recommended", [])}
            if tour.get("visitor_route"):
                card = render_route_card(public, grade)
                (staged / "route-card.jpg").write_bytes(card)
                receipt["route_card_sha256"] = sha(card)
            (staged / "manifest.json").write_bytes(encoded(normalized))
            (staged / "receipt.json").write_bytes(encoded(receipt))
            (staged / "tour.html").write_bytes(document)
            staged.rename(final)
        finally:
            if staged.exists():
                shutil.rmtree(staged)
        atomic(self.root / "latest", (revision + "\n").encode())
        return receipt
