"""Draw a phone-readable route card from the same views and anchors as the tour."""
import base64
import io
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


def photo(uri):
    with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as image:
        return image.convert("RGB")


def frame(image, pano, yaw, pitch, size):
    width, height = size
    plane = math.tan(math.radians(min(76, pano["haov"] * .8) / 2))
    x, y = np.meshgrid(np.linspace(-plane, plane, width), np.linspace(plane, -plane, height) * height / width)
    pitch = math.radians(pitch)
    z = math.cos(pitch) - y * math.sin(pitch)
    up = math.sin(pitch) + y * math.cos(pitch)
    lon = (np.degrees(np.arctan2(x, z)) + yaw + 180) % 360 - 180
    lat = np.degrees(np.arctan2(up, np.hypot(x, z)))
    cols = (lon / pano["haov"] + .5) * image.width - .5
    rows = (.5 - (lat - pano["vOffset"]) / pano["vaov"]) * image.height - .5
    return Image.fromarray(cv2.remap(np.asarray(image), cols.astype("float32"), rows.astype("float32"),
                                    cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP if pano["haov"] == 360
                                    else cv2.BORDER_CONSTANT))


def render(tour, grade):
    font_path = next((p for p in ("/System/Library/Fonts/Hiragino Sans GB.ttc",
                                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
                      if Path(p).is_file()), "Arial Unicode.ttf")
    fonts = {n: ImageFont.truetype(font_path, n) for n in (28, 31, 38, 44)}
    ink, green, muted = "#142d25", "#0b6149", "#52665e"
    stations = tour["stations"]
    card = Image.new("RGB", (720, 1400 + len(stations) * (1250 if "fixed_route" in tour else 900)), "#f5f8f6")
    draw = ImageDraw.Draw(card)
    y = 28

    def line(value, size=31, color=ink, left=28, width=664):
        nonlocal y
        font = fonts[size]
        row = ""
        for char in value.replace("\n", " "):
            if row and draw.textlength(row + char, font=font) > width:
                draw.text((left, y), row, font=font, fill=color)
                y += size + 8
                row = ""
            row += char
        draw.text((left, y), row, font=font, fill=color)
        y += size + 8

    def picture(item, size, position, direction=None):
        pano = item.get("panorama")
        width, height = size
        x, top = position
        if pano:
            view = direction or item.get("opening") or {}
            pitch = view.get("pitch", pano["vOffset"])
            lift = 0
            if direction:
                upper = pano["vOffset"] + pano["vaov"] / 2
                lift = max(0, min(9, upper - pitch - 18))
            image = frame(photo(pano["image"]), pano, view.get("yaw", 0), pitch + lift, size)
        elif item.get("originals"):
            original = photo(item["originals"][item.get("opening_photo", 0)]["image"])
            image = (ImageOps.contain(original, size) if "fixed_route" in tour else ImageOps.fit(original, size))
            position = (x + (width-image.width)//2, top + (height-image.height)//2)
        else:
            return
        card.paste(image, position)
        if direction and pano:
            plane = math.tan(math.radians(min(76, pano["haov"] * .8) / 2))
            cx = x + width // 2
            cy = top + round(height / 2 + width / 2 * math.tan(math.radians(lift)) / plane)
            draw.ellipse((cx-15, cy-15, cx+15, cy+15), fill=green, outline="white", width=2)
            draw.line((cx-6, cy+2, cx, cy-5, cx+6, cy+2), fill="white", width=3)

    if info := tour.get("fixed_route"):
        start = stations[0]
        direct = start["walk"].get(1, {})
        orientation = direct.get("orientation")
        if (info.get("public_photos") and grade["route_ready"] and not grade["reverse"]
                and len(stations) == 2 and orientation and (crop := start.get("sign_crop"))):
            line("步行路线 · 单向", 28, green)
            line(tour["title"], 44)
            y += 12
            line(f'1  {start["name"]}', 38, green)
            y += 12
            sign = ImageOps.contain(photo(crop["image"]), (664, 200))
            card.paste(sign, (28, y))
            y += sign.height + 16
            line(f'{orientation["road"]} {orientation["heading"]} {orientation["distance"]}', 44)
            line(f'左手边是{orientation["left"]}', 38)
            y += 18
            line(f'2  {stations[1]["name"]}', 38, green)
            line(info["destination"], 44)
            line(direct["arrive"], 38)
            y += 12
            line(f'公开资料核对 {info["checked_on"]} · 地面去程', 28, muted)
            credit = start["photo_sources"][crop["photo"]]
            line(f'出口牌原照裁切 · {credit["author"]} · {credit["license"]}', 28, muted)
            line("照片与官方交通资料见同版导览。", 28, muted)
            output = io.BytesIO()
            card.crop((0, 0, card.width, y+20)).save(output, "JPEG", quality=88, optimize=True)
            return output.getvalue()
        line(grade["label"], 28, green)
        line(tour["title"], 44)
        line(f'{info["start"]} → {info["destination"]}', 31)
        if grade["issue"]:
            line(grade["issue"] + info["verify_note"], 28, green)
        if not grade["issue"] or (info.get("public_photos") and info["access"] == "unknown"):
            if info["first_action"]:
                line("先用首张照片核对起点标牌。" if grade["issue"] else "第一步：" + info["first_action"], 31, green)
            if not grade["route_ready"]:
                line("试走到：" + stations[grade["through"]]["name"], 28, green)
            else:
                line("去程和回程均已核对" if grade["reverse"] else "单向路线指引", 28, green)
            for index, item in enumerate(stations):
                if index > grade["through"]:
                    break
                y += 12
                line(f'{index+1} · {item["name"]}', 38, green)
                step = item["walk"].get(index+1)
                if info.get("public_photos") and not step:
                    line("到这里停 · " + info["destination"], 31, green)
                photo_height = 445 if info.get("public_photos") else 330
                crop = item.get("sign_crop")
                if crop:
                    image = ImageOps.contain(photo(crop["image"]), (664, photo_height))
                    card.paste(image, (28, y))
                    photo_height = image.height
                else:
                    picture(item, (664, photo_height), (28, y))
                y += photo_height + 11
                if credits := item.get("photo_sources"):
                    credit = credits[crop["photo"] if crop else item["opening_photo"]]
                    line(("原照裁切 · " if crop else "") +
                         " · ".join(credit[k] for k in ("caption", "author", "license")), 28, muted)
                if step and step["confirmed"] and not grade["issue"]:
                    for key, caption in (("see", "看到"), ("action", "走法"), ("arrive", "走对后")):
                        line(caption + "：" + step[key], 28)
                elif step:
                    line(step.get("pending") or "本段走法待核对。", 28)
                elif not info.get("public_photos"):
                    line("到这里停：" + item["landmark"], 28)
            if grade["reverse"]:
                line("回程", 38, green)
                for i in range(len(stations)-1, 0, -1):
                    step = stations[i]["walk"][i-1]
                    line(stations[i]["name"] + "：认准" + step["see"] + "；" + step["action"] + "。看到" + step["arrive"] + "即走对。", 28)
            if info["verify_note"]:
                line(info["verify_note"], 28, muted)
        if info.get("public_photos"):
            line("公开资料核对，未现场踏勘。照片来源及许可链接见同版 HTML。", 28, muted)
            for item in stations[:grade["through"] + 1]:
                if credits := item.get("photo_sources"):
                    crop = item.get("sign_crop")
                    line(credits[crop["photo"] if crop else item["opening_photo"]]["url"], 28, muted)
        line("适用：" + info["audience"] + " · 核对：" + (info["checked_on"] or "待核对"), 28, muted)
        if info["coverage"]:
            line(info["coverage"], 28, muted)
        if tour.get("attribution"):
            line(tour["attribution"], 28, muted)
        output = io.BytesIO()
        card.crop((0, 0, card.width, y+20)).save(output, "JPEG", quality=86, optimize=True)
        return output.getvalue()

    ready = grade["route_ready"]
    entry = tour.get("entry", 0)
    line("第一次来 · 实景认路" if ready else "制作方试走 · 路线草稿", 28, green)
    line(tour["title"], 44)
    line(" → ".join(s["name"] for s in stations), 28, muted)
    y += 8
    opening = stations[entry]
    picture(opening, (664, 235), (28, y))
    y += 243
    line("起点 · " + (opening.get("landmark") or opening["name"]), 28, muted)
    path = list(range(len(stations)))
    headings = ("站点顺序", "反向浏览") if entry else ("去程", "原路返回")
    for label, order in zip(headings, (path, path[::-1])):
        if len(order) < 2:
            continue
        y += 10
        line(label, 38, green)
        for source, target in zip(order, order[1:]):
            item = stations[source]
            direction = next((link for link in item.get("links", [])
                              if link["to"] == target and "yaw" in link), None)
            top = y
            if direction:
                picture(item, (196, 117), (28, top), direction)
            left = 240 if direction else 28
            line(f"{item['name']} → {stations[target]['name']}", 31, left=left, width=692-left)
            if direction:
                note = direction.get("cue") or "在实景里找通道箭头"
            else:
                note = item.get("pending", {}).get(target) or "这段通道待确认，当前可浏览站点"
            line(note, 28, muted, left=left, width=692-left)
            y = max(y, top + 117 if direction else top) + 14
        if label == "去程":
            destination = stations[order[-1]]
            top = y + 5
            picture(destination, (196, 117), (28, top))
            y = top
            line("到这里停", 31, green, left=240, width=452)
            line(destination.get("landmark") or destination["name"], 28, muted, left=240, width=452)
            y = max(y, top + 117) + 14
    line("绿点标在实拍通道上。用导览 HTML 可转头试走。" if ready
         else "待确认方向先保留为站点浏览。", 28, muted)
    if tour.get("attribution"):
        y += 8
        line(str(tour["attribution"]), 28, muted)
    data = io.BytesIO()
    card.crop((0, 0, card.width, y+20)).save(data, "JPEG", quality=86, optimize=True)
    return data.getvalue()
