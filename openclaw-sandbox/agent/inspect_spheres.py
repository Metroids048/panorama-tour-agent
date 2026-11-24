"""Generate small, marked contact sheets from this conversation's photos."""
import argparse
import json
import math
from pathlib import Path
import warnings

from PIL import Image, ImageDraw, ImageOps

Image.MAX_IMAGE_PIXELS = 100_000_000
VIEW_WIDTH, VIEW_HEIGHT, VIEW_HEADER = 960, 640, 32

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("photos", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--view", nargs=2, type=float, metavar=("YAW", "PITCH"),
                    help="inspect the exact center of a proposed arrow on one full sphere")
parser.add_argument("--point", nargs=2, type=int, metavar=("X", "Y"),
                    help="project a selected pixel in anchor.jpg and render a centered check image")
parser.add_argument("--route-index", action="store_true",
                    help="show the full turn in one numbered direction sheet for one sphere")
args = parser.parse_args()
single_file = args.photos.is_file()
if args.route_index and (not single_file or args.view is not None or args.point is not None):
    parser.error("--route-index needs one sphere and no --view or --point")
if args.view is not None:
    yaw, pitch = args.view
    if not single_file or not (-180 <= yaw <= 180 and -90 <= pitch <= 90):
        parser.error("--view needs one sphere and an in-range yaw/pitch")
if args.point is not None:
    point_x, point_y = args.point
    if args.view is None or not (0 <= point_x < VIEW_WIDTH
                                 and VIEW_HEADER <= point_y < VIEW_HEADER + VIEW_HEIGHT):
        parser.error("--point requires --view and a pixel inside anchor.jpg below its header")
rows = []
paths = [args.photos] if single_file else sorted(args.photos.rglob("*"))
for path in paths:
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        continue
    error = None
    width = height = 0
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as original:
                if original.format not in {"JPEG", "MPO", "PNG", "WEBP"}:
                    raise ValueError("unsupported image format")
                image = ImageOps.exif_transpose(original).convert("RGB")
                width, height = image.size
                image.thumbnail((1440, 720))
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as ex:
        error = ("图片像素超过 100 MP（1 亿像素）处理上限，请只替换这一张。"
                 if isinstance(ex, (Image.DecompressionBombError, Image.DecompressionBombWarning))
                 else "图片无法解码，原件已保留；请只重发或替换这一张 JPG、PNG 或 WebP。")
        width = height = 0
        image = Image.new("RGB", (800, 160), "#442b24")
        ImageDraw.Draw(image).text((18, 55), "Image unreadable - original retained; replace this file only", fill="white")
    name = path.name if single_file else str(path.relative_to(args.photos))
    spherical = not error and abs(width - 2 * height) <= max(2, width * 0.002)
    if (args.view is not None or args.route_index) and not spherical:
        parser.error("a route view needs a readable 2:1 sphere after applying camera orientation")
    sheet = Image.new("RGB", (image.width, image.height + 40), "#141d1a")
    sheet.paste(image, (0, 40))
    draw = ImageDraw.Draw(sheet)
    draw.text((8, 4), f"{len(rows)+1} | {width}x{height}", fill="white")
    if spherical:
        for yaw in range(-180, 180, 30):
            x = (yaw + 180) * image.width // 360
            draw.line((x, 38, x, 48), fill="white", width=2)
            draw.text((x + 2, 23), str(yaw), fill="white")
        for pitch in (60, 30, 15, 0, -15, -30, -60):
            y = 40 + (90 - pitch) * image.height // 180
            draw.line((0, y, 10, y), fill="white", width=2)
            draw.text((12, y - 6), f"{pitch:+d}", fill="white", stroke_width=2, stroke_fill="#141d1a")
    args.output.mkdir(parents=True, exist_ok=True)
    out = args.output / f"{len(rows)+1}.jpg"
    sheet.save(out, quality=83)
    rows.append({"file": name, "preview": str(out.resolve()), "width": width, "height": height,
                 **({"error": error} if error else {})})
    # A single-file request inspects a large sphere without sending the full
    # 98 MP file to the model. These are view samples, never route answers.
    if single_file and spherical:
        import cv2
        import numpy as np

        with Image.open(path) as source:
            source = ImageOps.exif_transpose(source).convert("RGB")
            source.thumbnail((8192, 4096), Image.Resampling.LANCZOS)
            pixels = np.asarray(source)
        view_width, view_height, hfov = VIEW_WIDTH, VIEW_HEIGHT, 90
        plane = math.tan(math.radians(hfov / 2))
        x, up = np.meshgrid(np.linspace(-plane, plane, view_width),
                            np.linspace(plane, -plane, view_height) * view_height / view_width)
        center = (view_width // 2, view_height // 2 + VIEW_HEADER)

        def render_view(yaw, pitch, target, *, mark_center=False, point=None):
            angle = math.radians(pitch)
            vertical = up * math.cos(angle) + math.sin(angle)
            forward = math.cos(angle) - up * math.sin(angle)
            latitude = np.degrees(np.arctan2(vertical, np.sqrt(x*x + forward*forward)))
            longitude = (np.degrees(np.arctan2(x, forward)) + yaw + 180) % 360 - 180
            cols = ((longitude / 360 + .5) * pixels.shape[1] - .5).astype("float32")
            lines = np.clip((.5 - latitude / 180) * pixels.shape[0] - .5, 0, pixels.shape[0] - 1).astype("float32")
            view = Image.fromarray(cv2.remap(pixels, cols, lines, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP))
            marked = Image.new("RGB", (view_width, view_height + VIEW_HEADER), "#141d1a")
            marked.paste(view, (0, VIEW_HEADER))
            draw = ImageDraw.Draw(marked)
            draw.text((10, 8), f"yaw {yaw:+g} | pitch {pitch:g} | hfov 90 | inspection sample", fill="white")
            if point is not None or mark_center:
                x0, y0 = point or center
                draw.ellipse((x0-14, y0-14, x0+14, y0+14), outline="#101615", width=5)
                draw.ellipse((x0-14, y0-14, x0+14, y0+14), outline="white", width=2)
            if target is None:
                return marked
            marked.save(target, quality=92)
            result = {"file": name, "preview": str(target.resolve()), "yaw": yaw, "pitch": pitch,
                      "hfov": hfov, "inspection_only": True}
            if point is not None:
                x0, y0 = point
                row = y0 - VIEW_HEADER
                result["anchor_yaw"] = round(float(longitude[row, x0]), 6)
                result["anchor_pitch"] = round(float(latitude[row, x0]), 6)
            return result

        if args.route_index:
            yaws = tuple(range(-180, 180, 45))
            pitch = -12
            tile_width, tile_height = 480, 336
            index = Image.new("RGB", (tile_width * 4, tile_height * 2))
            for cell, yaw in enumerate(yaws):
                tile = render_view(yaw, pitch, None).resize((tile_width, tile_height), Image.Resampling.LANCZOS)
                label = ImageDraw.Draw(tile)
                label.rectangle((0, 0, tile_width, 22), fill="#141d1a")
                label.text((8, 3), f"YAW {yaw:+d}  PITCH {pitch:d}", fill="white", font_size=15)
                index.paste(tile, ((cell % 4) * tile_width, (cell // 4) * tile_height))
            target = args.output / "route-index.jpg"
            index.save(target, quality=89)
            rows.append({"file": name, "route_index": str(target.resolve()), "yaws": yaws,
                         "pitch": pitch, "inspection_only": True})
        elif args.view is None:
            for pitch in (0, -45):
                suffix = "-down45" if pitch else ""
                for yaw in range(-180, 180, 45):
                    rows.append(render_view(yaw, pitch, args.output / f"view-{yaw:+g}{suffix}.jpg"))
        else:
            yaw, pitch = args.view
            result = render_view(yaw, pitch, args.output / "anchor.jpg", mark_center=True, point=args.point)
            if args.point is not None:
                centered = render_view(result["anchor_yaw"], result["anchor_pitch"],
                                       args.output / "centered.jpg", mark_center=True)
                result.update(point=args.point, centered_preview=centered["preview"])
            rows.append(result)
print(json.dumps(rows, ensure_ascii=False))
