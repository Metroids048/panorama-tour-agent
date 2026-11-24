"""Compile a venue overview from the saved manifest, independently of station order."""
import base64
import io
import math
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, features


def note(value, field, limit=220):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"请填写{field}（最多 {limit} 字）")
    return value.strip()


def rows(value, field, minimum, maximum):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{field}应有 {minimum}–{maximum} 项")
    return value


def pin(row):
    if not any(k in row for k in ("x", "y")):
        return {}
    if any(type(row.get(k)) not in (int, float) or not math.isfinite(row[k]) or not 0 <= row[k] <= 1
           for k in ("x", "y")):
        raise ValueError("平面位置须同时提供 0–1 的 x 和 y")
    return {"x": row["x"], "y": row["y"]}


def plan_image(data, crop=None):
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 24_000_000:
            raise ValueError("平面图须为不超过 2400 万像素的 JPEG、PNG 或 WebP")
        image = ImageOps.exif_transpose(image).convert("RGB")
        if crop:
            image = image.crop(tuple(round(v * dim) for v, dim in zip(crop, image.size * 2)))
        image.thumbnail((1800, 1800))
        out = io.BytesIO()
        image.save(out, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def scene_preview(station):
    """A small perspective view of the photographed opening, never a stretched sphere."""
    pano = station["panorama"]
    if not pano or (pano["haov"], pano["vaov"]) != (360, 180):
        return station["originals"][0]["image"]
    pixels = np.frombuffer(base64.b64decode(pano["image"].split(",", 1)[1]), dtype=np.uint8)
    sphere = cv2.imdecode(pixels, cv2.IMREAD_COLOR)
    height, width = sphere.shape[:2]
    opening = station.get("opening") or (station["links"] or [{}])[0]
    yaw, pitch = np.radians([opening.get("yaw", 0), opening.get("pitch", 0)])
    x, y = np.meshgrid((np.arange(512) + .5 - 256) / 340,
                       (144 - np.arange(288) - .5) / 340)
    forward = np.cos(pitch) - y * np.sin(pitch)
    angle = (np.arctan2(x, forward) + yaw + np.pi) % (2 * np.pi)
    elevation = np.arctan2(np.sin(pitch) + y * np.cos(pitch), np.hypot(x, forward))
    image = cv2.remap(sphere, (angle * width / (2 * np.pi) - .5).astype("float32"),
                      np.clip((.5 - elevation / np.pi) * height - .5, 0, height - 1).astype("float32"),
                      cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 84])
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode() if ok else ""


def portable_sphere(pano, width, preserve_source=False):
    """Keep the full view and package a smaller, ready-to-render texture for touch devices."""
    if not pano or (pano["haov"], pano["vaov"]) != (360, 180) or width <= 4096:
        return pano
    original = base64.b64decode(pano["image"].split(",", 1)[1])
    webp = features.check("webp")

    def encode(image, quality):
        output = io.BytesIO()
        image.save(output, "WEBP" if webp else "JPEG", quality=quality)
        return output.getvalue()

    def data_url(data):
        return "data:image/" + ("webp" if webp else "jpeg") + ";base64," + base64.b64encode(data).decode()

    with Image.open(io.BytesIO(original)) as sphere:
        compact = sphere.resize((3840, 1920), Image.Resampling.LANCZOS)
        compact_data = encode(compact, 87)
        # Only replace the full-size encoding when the combined payload gets smaller.
        full_data = encode(sphere, 89) if webp and not preserve_source else original
    if len(full_data) + len(compact_data) < len(original):
        pano = dict(pano, image=data_url(full_data))
    return dict(pano, compact={"image": data_url(compact_data), "width": 3840, "height": 1920})


def map_kind(spec):
    value = spec.get("kind", "located")
    if not isinstance(value, str) or value not in {"located", "schematic"}:
        raise ValueError("地图类型须为 located 或 schematic")
    return value


def map_labels(spec):
    value = spec.get("labels_embedded", False)
    if type(value) is not bool:
        raise ValueError("labels_embedded 须为 true 或 false")
    return {"labels_embedded": value}


def map_plan(spec, asset):
    """Render declared, reviewed space relationships without executing agent code."""
    if "schematic" not in spec:
        if not isinstance(spec.get("plan"), str):
            raise ValueError("地图需要图片路径或已核实的空间示意与点位")
        return plan_image(asset(spec["plan"]))
    if map_kind(spec) != "schematic" or spec.get("plan"):
        raise ValueError("空间示意须用 kind:schematic，且不能同时指定 plan")
    if spec.get("plan_crop") is not None:
        raise ValueError("空间示意使用自身坐标，不能套用图片裁边")
    drawing = spec["schematic"]
    if not isinstance(drawing, dict):
        raise ValueError("空间示意需要 areas 和可选 paths")

    def points(value, minimum):
        result = []
        for point in rows(value, "示意坐标", minimum, 40):
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("示意坐标须为 [x,y]")
            pin({"x": point[0], "y": point[1]})
            result.append((round(point[0] * 1200), round(point[1] * 900)))
        return result

    image = Image.new("RGB", (1200, 900), "#f5f3eb")
    draw = ImageDraw.Draw(image)
    font_path = next((p for p in ("/System/Library/Fonts/Hiragino Sans GB.ttc",
                                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
                      if Path(p).is_file()), "Arial Unicode.ttf")
    font = ImageFont.truetype(font_path, 28)
    for area in rows(drawing.get("areas"), "已核实空间区域", 1, 32):
        if not isinstance(area, dict):
            raise ValueError("空间区域需要 name 和 polygon")
        name = note(area.get("name"), "区域名称", 30)
        polygon = points(area.get("polygon"), 3)
        draw.polygon(polygon, fill="#e0e9df", outline="#778d7b", width=4)
        left, top = min(x for x, y in polygon), min(y for x, y in polygon)
        draw.text((left + 12, top + 10), name, fill="#344a3b", font=font)
    for line in rows(drawing.get("paths", []), "已核实通道示意", 0, 64):
        draw.line(points(line, 2), fill="#b7a37a", width=16, joint="curve")
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def navigation_map(spec, ids, asset, stations=None):
    """A supplied plan with explicitly located scene IDs, embedded for offline use."""
    if not isinstance(spec, dict):
        raise ValueError("地图需要图片路径和已确认点位")
    result = {"name": note(spec.get("name"), "地图名称", 70),
              "source": note(spec.get("source"), "地图来源及定位依据"),
              "plan": map_plan(spec, asset), "places": [], "kind": map_kind(spec), **map_labels(spec)}
    located = set()
    for place in rows(spec.get("places"), "地图点位", 1, len(ids)):
        if not isinstance(place, dict):
            raise ValueError("地图点位需要站点、x 和 y")
        key = place.get("station")
        if key not in ids or key in located:
            raise ValueError("地图点位须唯一并指向已知站点")
        point = pin(place)
        if not point:
            raise ValueError("地图点位需要 x、y 定位")
        # Coordinates imported from a photograph remain tied to that input,
        # rather than silently following a reordered station list.
        if "source_file" in place:
            station = next((s for s in stations or [] if s["id"] == key), None)
            if not station or place["source_file"] not in station.get("files", []):
                raise ValueError("地图坐标依据原片与站点不匹配，请按实际原片重新定位")
        located.add(key)
        result["places"].append({"station": ids.index(key), **point})
    return result


def overview(spec, stations, asset, identifier):
    coverage = note(spec.get("coverage"), "实际覆盖范围")
    require_cross_floor = spec.get("require_cross_floor", False)
    if type(require_cross_floor) is not bool:
        raise ValueError("require_cross_floor 须为 true 或 false")
    ids = {s["id"]: i for i, s in enumerate(stations)}
    levels, crops = {}, {}
    for level in rows(spec.get("levels"), "楼层或分区", 1, 10):
        key = identifier(level["id"])
        if key in levels:
            raise ValueError("楼层标识不能重复")
        row = {"id": key, "name": note(level.get("name"), "楼层名", 70), "places": []}
        if "floor" in level:
            row["floor"] = note(level["floor"], "物理楼层", 30)
        if level.get("plan") or "schematic" in level:
            row["source"] = note(level.get("source"), "平面图来源及使用依据")
            crop = level.get("plan_crop")
            if crop is not None:
                if (not isinstance(crop, list) or len(crop) != 4 or
                        any(type(v) not in (int, float) or not 0 <= v <= 1 for v in crop) or
                        crop[2] - crop[0] < .1 or crop[3] - crop[1] < .1):
                    raise ValueError("平面裁边须为有效的 0–1 坐标")
                crops[key] = crop
            row["plan"] = map_plan(level, asset) if "schematic" in level else plan_image(asset(level["plan"]), crop)
            row["kind"] = map_kind(level)
            row.update(map_labels(level))
        for area in rows(level.get("areas", []), "仅示意区域", 0, 20):
            row["places"].append({"name": note(area.get("name"), "区域名", 70),
                                  "note": note(area.get("note"), "无实景或未开放的原因"), **pin(area)})
        levels[key] = row
    if len({row["name"] for row in levels.values()}) != len(levels):
        raise ValueError("楼层显示名称不能重复")
    placed = set()
    places = {}
    for p in rows(spec.get("places", []), "实景点位定位", 0, len(ids)):
        station, level = identifier(p["station"]), identifier(p["level"])
        if station not in ids or station in placed or level not in levels:
            raise ValueError("每个实景点位只能绑定一个已知楼层")
        placed.add(station)
        place = {"station": ids[station], "level": level, **pin(p)}
        places[station] = place
        levels[level]["places"].append(place)
    for key, (left, top, right, bottom) in crops.items():
        for place in levels[key]["places"]:
            if "x" in place:
                if not (left <= place["x"] <= right and top <= place["y"] <= bottom):
                    raise ValueError("平面裁边不能裁掉已定位的点")
                place["x"] = (place["x"] - left) / (right - left)
                place["y"] = (place["y"] - top) / (bottom - top)
    # Unlocated shots remain available without guessing a floor.
    if set(ids) - placed:
        levels[""] = {"id": "", "name": "楼层待核对", "places": [
            {"station": i, "level": ""} for s, i in ids.items() if s not in placed]}
    connections = {}
    for c in rows(spec.get("connections", []), "方向连接", 0, 256):
        source, dest = identifier(c["from"]), identifier(c["to"])
        if source not in ids or dest not in ids or source == dest or (source, dest) in connections:
            raise ValueError("方向连接必须唯一并指向另一已知站点")
        row = {"from": ids[source], "to": ids[dest], "name": note(c.get("name"), "连接名称", 70)}
        if c.get("pending"):
            row["pending"] = note(c["pending"], "待核对事项")
        else:
            for key, label in (("access", "通行限制"), ("hours", "有效时间"), ("audience", "适用人群")):
                row[key] = note(c.get(key), label)
        connections[source, dest] = row
    recommendations = rows(spec.get("recommended", []), "推荐看点", 0, len(ids))
    if any(identifier(s) not in ids for s in recommendations) or len(set(recommendations)) != len(recommendations):
        raise ValueError("推荐看点须指向不重复的已知实景")
    opening = spec.get("opening_level", next(iter(levels)))
    if not isinstance(opening, str) or opening not in levels or not opening:
        raise ValueError("开场楼层须为已知楼层")
    return {"levels": list(levels.values()), "coverage": coverage, "opening_level": opening,
            "require_cross_floor": require_cross_floor,
            "overview_note": note(spec["overview_note"], "总览提示", 90) if spec.get("overview_note") else "",
            "recommended": [ids[s] for s in recommendations], "connections": list(connections.values())}, connections


def quality(overview, stations, entry):
    reasons = []
    if any(not level["id"] for level in overview["levels"]):
        reasons.append("有实景点位的楼层待核对。")
    levels = {p["station"]: level["id"] for level in overview["levels"] for p in level["places"] if "station" in p}
    directions = overview["connections"]
    outgoing, neighbors = defaultdict(set), defaultdict(set)
    active = [c for c in directions if not c.get("pending")]
    for c in active:
        a, b = c["from"], c["to"]
        outgoing[a].add(b)
        neighbors[a].add(b)
        neighbors[b].add(a)
    visited, queue = set(), [entry]
    while queue:
        point = queue.pop()
        if point not in visited:
            visited.add(point)
            queue.extend(outgoing[point] - visited)
    # The viewer already renders an explicitly non-metric graph when no plan
    # exists. Only reachable, confirmed connections may locate its points;
    # a supplied measured plan still requires actual coordinates.
    if any((any("station" in p and "x" not in p for p in level["places"])
            if level.get("plan") else
            any(p["station"] not in visited for p in level["places"] if "station" in p))
           for level in overview["levels"] if level["id"]):
        reasons.append("有楼层缺少平面或已核对的通道示意定位。")
    if not any(c["from"] in visited and levels[c["from"]] and levels[c["to"]]
               and levels[c["from"]] != levels[c["to"]] for c in active):
        reasons.append("从入口还不能沿已核对方向到达另一楼层或分区。")
    floors = {level["id"]: level.get("floor") for level in overview["levels"]}
    cross_floor_reasons = []
    if not all(floors.values()):
        cross_floor_reasons.append("跨楼层交付须核对每个分区所在的物理楼层。")
    if not any(c["from"] in visited and floors[levels[c["from"]]] and floors[levels[c["to"]]]
               and floors[levels[c["from"]]] != floors[levels[c["to"]]] for c in active):
        cross_floor_reasons.append("缺少从入口可到达、已核对的物理跨楼层连接。")
    if not any(len(neighbors[p]) >= 3 and len(outgoing[p]) >= 2 for p in visited):
        reasons.append("从入口还不能走到已核对的分支。")
    if len(active) < len(directions):
        reasons.append("仍有已列出的通道方向待核对。")
    for i in {c[key] for c in active for key in ("from", "to")}:
        s = stations[i]
        p = s.get("view") or {}
        h, v = p.get("haov", 0), p.get("vaov", 0)
        if h < 180 or v < 35 or p.get("width", 0) < 8*h or p.get("height", 0) < max(480, 8*v):
            reasons.append(f'{s["name"]}：可走点位的全景覆盖或清晰度不足。')
    single_floor = bool(floors) and all(floors.values()) and len(set(floors.values())) == 1
    panorama_reasons = list(reasons)
    if set(range(len(stations))) - visited:
        panorama_reasons.append("有实景点无法从入口沿已核对方向到达。")
    if overview.get("require_cross_floor") or not single_floor:
        panorama_reasons.extend(cross_floor_reasons)
    if overview.get("require_cross_floor"):
        reasons = panorama_reasons
    label = "场馆导览 · 跨层总览" if not panorama_reasons else "场馆导览 · 分区总览"
    if single_floor:
        label = "场馆导览 · 单层分区"
    return dict(kind="venue-preview" if reasons else "venue-tour",
                label="场馆总览 · 预览" if reasons else label,
                route_ready=not reasons, reasons=reasons, arrows=len(active), cuts=0, required=len(directions),
                panorama_ready=not panorama_reasons, panorama_reasons=panorama_reasons)
