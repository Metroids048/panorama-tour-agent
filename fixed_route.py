"""Evidence and release checks for a fixed walking route."""
import base64
import io
from datetime import date, datetime
from html import escape
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from PIL import Image


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def date_text(value):
    try:
        return date.fromisoformat(value).isoformat() == value
    except (TypeError, ValueError):
        return False


def words(data, key, limit=100):
    value = data.get(key, "")
    return value.strip()[:limit] if isinstance(value, str) else ""


def https_url(data, key):
    value = words(data, key, 500)
    try:
        return value if urlsplit(value).scheme == "https" and urlsplit(value).hostname else ""
    except ValueError:
        return ""


def public_photos(raw, count):
    if not isinstance(raw, list) or len(raw) != count:
        return []
    sources = []
    for item in raw:
        if not isinstance(item, dict):
            return []
        source = {key: words(item, key) for key in ("author", "license", "caption")}
        source.update(url=https_url(item, "url"), license_url=https_url(item, "license_url"))
        if not all(source.values()):
            return []
        sources.append(source)
    return sources


def photo_uri(image, quality):
    output = io.BytesIO()
    image.save(output, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def sign_crop(raw, files, originals):
    if not isinstance(raw, dict) or raw.get("asset") not in files:
        raise ValueError("标牌局部须绑定本站已保留的原照")
    box = raw.get("box")
    if (not isinstance(box, list) or len(box) != 4 or
            any(type(v) not in (int, float) or not 0 <= v <= 1 for v in box) or
            not (box[0] < box[2] and box[1] < box[3])):
        raise ValueError("标牌局部须使用有效的原图相对坐标")
    index = files.index(raw["asset"])
    data = base64.b64decode(originals[index]["image"].split(",", 1)[1])
    with Image.open(io.BytesIO(data)) as image:
        x0, y0, x1, y1 = [round(v * size) for v, size in zip(box, image.size * 2)]
        if x1 - x0 < 64 or y1 - y0 < 24:
            raise ValueError("标牌局部像素不足")
        detail = image.crop((x0, y0, x1, y1))
        detail.thumbnail((1400, 900), Image.Resampling.LANCZOS)
    return {"image": photo_uri(detail, 92),
            "photo": index, "width": detail.width, "height": detail.height}


def public_route_page(tour):
    """A photo route whose instructions and page jumps work without JavaScript."""
    info, stations = tour["fixed_route"], tour["stations"]
    markup = []

    def picture(item, index, crop=None, lazy=False):
        asset, credit = item["originals"][index], item["photo_sources"][index]
        method = "（原照裁切）" if crop else ""
        if crop:
            src, width, height = crop["image"], crop["width"], crop["height"]
        else:
            with Image.open(io.BytesIO(base64.b64decode(asset["image"].split(",", 1)[1]))) as image:
                image.thumbnail((1440, 1440), Image.Resampling.LANCZOS)
                src, (width, height) = photo_uri(image, 85), image.size
        loading = ' loading="lazy"' if lazy else ""
        return (f'<figure><img src="{escape(src)}" alt="{escape(item["landmark"] + method)}"'
                f' width="{width}" height="{height}"{loading}>'
                f'<figcaption>{escape(credit["caption"] + method)} · '
                f'<a href="{escape(credit["url"])}">{escape(credit["author"])}</a> · '
                f'<a href="{escape(credit["license_url"])}">{escape(credit["license"])}</a></figcaption></figure>')

    for i, item in enumerate(stations):
        opening = item["opening_photo"]
        crop = item.get("sign_crop")
        step = item["walk"].get(i + 1)
        title = escape(item["name"])
        action = escape(step["action"] if step else "到这里停 · " + info["destination"])
        markup.append(f'<article id="stop-{i}" class="shell"><div class="walk-copy"><div class="eyebrow">第 {i+1} 步 / {len(stations)}</div>'
                      f'<h2>{title}</h2><p class="walk-action">{action}</p>')
        if step:
            markup.append(f'<p>{escape(step["see"])}</p>')
        elif i:
            markup.append(f'<p>{escape(stations[i-1]["walk"][i]["arrive"])}</p>')
        markup.append('</div>')
        if crop:
            markup.append(picture(item, crop["photo"], crop))
        elif step:
            markup.append(picture(item, opening, lazy=i > 0))
        if step:
            if direction := step.get("orientation"):
                road, heading, distance, left = (escape(direction[key]) for key in ("road", "heading", "distance", "left"))
                markup.append(f'<div class="walk-orientation"><p><strong>左手边</strong>{left}</p>'
                              f'<p><span aria-hidden="true">↑</span><strong>{road} {heading}</strong>{distance}</p>'
                              '<small>按行走方向示意 · 非地图</small></div>')
            next_label = "查看终点" if i == len(stations) - 2 else "查看下一步"
            markup.append(f'<a class="download walk-next" href="#stop-{i+1}">{next_label} · {escape(stations[i+1]["name"])}</a>')
        elif i:
            markup.append(f'<a class="walk-back" href="#stop-{i-1}">回看{"起点" if i == 1 else "上一步"}</a>')
        images = [j for j in range(len(item["originals"])) if crop or not step or j != opening]
        if images:
            markup.append('<details><summary>' + ("看完整照片" if crop or step else "看场景参考照片") + '</summary>')
            if not step and not crop:
                markup.append('<p class="walk-reference">照片只供辨认周围环境。请按入口名称和地址核对。</p>')
            markup.extend(picture(item, j, lazy=True) for j in images)
            markup.append('</details>')
        markup.append('</article>')
    markup.append(f'<details class="walk-info"><summary>适用范围与来源</summary><p>{escape(info["audience"])} · '
                  f'{escape(info["coverage"])} · 核对 {escape(info["checked_on"])}</p><p>{escape(info["verify_note"])}</p>'
                  f'<a href="{escape(info["verify_url"])}">查看官方公告（需联网）</a></details>')
    return (f'<main class="body walk-page" id="tour"><p class="walk-file">单向路线 · 保存此 HTML 后可离线打开</p>'
            f'{"".join(markup)}</main>')


def route_info(raw):
    fields = {"start": 80, "destination": 80, "audience": 80, "coverage": 120,
              "checked_on": 10, "valid_from": 10, "valid_until": 10, "access": 30,
              "verify_note": 120, "first_action": 100, "visit_mode": 20, "captured_on": 10}
    out = {k: words(raw, k, limit) for k, limit in fields.items()}
    if url := https_url(raw, "verify_url"):
        out["verify_url"] = url
    if raw.get("public_photos") is True:
        out["public_photos"] = True
    return out


def access_issue(info, day=None):
    day = day or today()
    if info.get("visit_mode") == "virtual":
        if (info.get("public_photos") is not True or info["access"] != "historical" or
                not date_text(info.get("captured_on")) or info["captured_on"] > day or
                info["valid_from"] or info["valid_until"]):
            return "历史虚拟参观须有授权公开原图和拍摄日期，不得承诺当前现场通行。"
        return ""
    if info.get("visit_mode") not in (None, "", "onsite"):
        return "路线参观方式未核实。"
    if info["access"] != "open":
        return {"closed": "现场入口或通道不可用。", "wrong_start": "当前起点和这条路线不匹配。",
                "ticket_unknown": "票区或适用入口未确认。"}.get(info["access"], "开放或通行状态未核实。")
    start, end = info["valid_from"], info["valid_until"]
    if (start and not date_text(start)) or (end and not date_text(end)) or (start and end and start > end):
        return "路线适用日期未核实。"
    if (start and day < start) or (end and day > end):
        return "这条路线不在适用日期内。"
    return ""


def grade_route(tour):
    info, stations = tour["fixed_route"], tour["stations"]
    public = info.get("public_photos", False)
    issue = access_issue(info)
    required_info = ("start", "destination", "audience", "coverage", "first_action", "verify_url", "verify_note")
    missing_info = any(not info.get(k) for k in required_info)
    forward, reverse = [], []
    for i, (source, dest) in enumerate(zip(stations, stations[1:])):
        forward.append(source["walk"].get(i + 1, {}))
        reverse.append(dest["walk"].get(i, {}))
    gap = next((i for i, direction in enumerate(forward) if not direction["confirmed"]), None)
    reasons = []
    if issue:
        reasons.append(issue)
    if missing_info or not date_text(info["checked_on"]) or info["checked_on"] > today():
        reasons.append("请补齐核对日期、适用人群、起终点、第一步、覆盖范围和核实入口。")
    spheres = all(s.get("panorama") and not s.get("use_originals") for s in stations)
    if info.get("visit_mode") == "virtual" and not spheres:
        reasons.append("历史虚拟参观须逐站保留完整实拍全景。")
    maximum = 32 if spheres else 5
    if public and not 2 <= len(stations) <= maximum:
        reasons.append("公开照片路线需 2–5 个有依据的决策点，含起终点。")
    elif not public and not 3 <= len(stations) <= maximum:
        reasons.append("成品需 3–5 个有实拍的决策点，含起终点。")
    if public and any(not station["photo_sources"] for station in stations):
        reasons.append("公开照片需逐张注明作者、转载许可、来源和画面用途。")
    if not public and not info.get("survey_record"):
        reasons.append("缺少同日连续实拍、来源授权或踏勘记录。")
    if gap is not None:
        reasons.append(forward[gap].get("pending") or "有一段去程待核对。")
    ready = not reasons
    label = ("历史虚拟参观" if info.get("visit_mode") == "virtual" else
             "公开照片路线" if public else "实景步行路线") if ready else "路线草稿"
    return dict(kind="fixed", label=label, route_ready=ready, public_photos=public,
                reasons=reasons, gap=gap, blocked=bool(issue), issue=issue,
                through=0 if issue else gap if gap is not None else len(stations) - 1,
                reverse=ready and all(direction["confirmed"] for direction in reverse),
                arrows=sum(s["confirmed"] for s in forward + reverse), cuts=0,
                required=len(forward))
