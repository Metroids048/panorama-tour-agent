"""Compose photographed coverage into a bounded spherical texture."""
import base64
import hashlib
import io
import math
import warnings

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_STATION_PHOTOS = 24

cv2.setNumThreads(2)
Image.MAX_IMAGE_PIXELS = 100_000_000


def decode(data, spherical=False, *, max_edge=2400):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            if source.format == "MPO":
                # Some camera JPEGs include auxiliary images. Only show the primary photograph.
                source.seek(0)
            elif source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("请使用 JPG、PNG 或 WebP")
            im = ImageOps.exif_transpose(source)
            if spherical:
                delta = abs(im.width - 2 * im.height)
                if delta > max(2, im.width * 0.002):
                    raise ValueError("完整球面图应为 2:1 等距柱状照片")
                if delta > 2:
                    im = im.resize((im.width, round(im.width / 2)), Image.Resampling.LANCZOS)
            im.thumbnail((3840, 1920) if spherical else (max_edge, max_edge), Image.Resampling.LANCZOS)
            return cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2BGR)


def jpeg(img):
    ok, data = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise ValueError("图片编码失败")
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")

def bounded_texture(img):
    h, w = img.shape[:2]
    if max(h, w) <= 4096:
        return img
    scale = 4096 / max(h, w)
    return cv2.resize(img, (round(w*scale), round(h*scale)), interpolation=cv2.INTER_AREA)


def covered_rectangle(mask):
    """Find the largest fully covered rectangle on a 4-pixel grid."""
    h, w = mask.shape
    h -= h % 4
    w -= w % 4
    if not h or not w:
        raise ValueError("覆盖范围太小")
    grid = mask[:h, :w].reshape(h // 4, 4, w // 4, 4).all(axis=(1, 3))
    heights = np.zeros(w // 4, dtype=int)
    best = (0, 0, 0, 0, 0)
    for y, row in enumerate(grid, 1):
        heights = (heights + 1) * row
        stack = []
        for x, v in enumerate([*heights, 0]):
            start = x
            while stack and stack[-1][1] > v:
                start, height = stack.pop()
                area = height * (x - start)
                if area > best[0]:
                    best = (area, start, y - height, x, y)
            if not stack or stack[-1][1] < v:
                stack.append((start, v))
    if not best[0]:
        raise ValueError("没有连续实拍范围")
    return tuple(int(4 * i) for i in best[1:])


class ProjectionBudgetExceeded(ValueError):
    pass


def stitch(images):
    # Keep the strict pass independent of previous stations processed by this thread.
    cv2.setRNGSeed(23)
    s = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
    status = s.estimateTransform(images)
    if status:
        return None, [], "相邻照片没有找到足够可靠的共同画面。"
    indices = s.component().tolist()
    if len(indices) != len(images):
        return None, indices, "只有部分照片能接起来；请检查未匹配的照片。"
    cameras = s.cameras()
    ratio = 1 / s.workScale()  # Compose at full size after the upload's initial downscale.
    scale = float(np.float32(np.median([c.focal for c in cameras]) * ratio))
    if not math.isfinite(scale) or scale < 30 or scale > 15000:
        raise ValueError("无法可靠估计视野")
    warper = cv2.PyRotationWarper("spherical", scale)
    params = []
    for i, cam in zip(indices, cameras):
        k = cam.K().astype(np.float32)
        k[:2] *= ratio
        r = cam.R.astype(np.float32)
        h, w = images[i].shape[:2]
        x, y, rw, rh = warper.warpRoi((w, h), k, r)
        params.append((h, w, k, r, x, y, rw, rh))
    x0 = min(p[4] for p in params)
    y0 = min(p[5] for p in params)
    w = max(p[4] + p[6] for p in params) - x0
    h = max(p[5] + p[7] for p in params) - y0
    if w <= 0 or h <= 0:
        raise ValueError("无法确认拼接画面的投影范围")
    if w > 6500 or h > 3800 or w * h > 9_000_000:
        raise ProjectionBudgetExceeded("投影尺寸超过合成预算")
    status, pano = s.composePanorama()
    if status or pano.shape[:2] != (h, w):
        raise ValueError("无法确认拼接画面的投影范围")
    union = np.zeros((h, w), dtype=np.uint8)
    for ih, iw, k, r, x, y, rw, rh in params:
        origin, mask = warper.warp(np.ones((ih, iw), dtype=np.uint8), k, r, cv2.INTER_NEAREST, cv2.BORDER_CONSTANT)
        if origin != (x, y) or mask.shape != (rh, rw):
            raise ValueError("无法确认实拍边界")
        union[y-y0:y-y0+rh, x-x0:x-x0+rw] |= mask
    left, top, right, bottom = covered_rectangle(union)
    if (right - left) * (bottom - top) < w * h * .55:
        raise ValueError("相连的实拍范围太小")
    haov, vaov = math.degrees((right-left)/scale), math.degrees((bottom-top)/scale)
    offset = 90 - math.degrees((y0+(top+bottom)/2)/scale)
    if not (20 <= haov <= 340 and 15 <= vaov <= 160 and abs(offset) + vaov / 2 <= 90.5):
        raise ValueError("无法可靠限制拖看的范围")
    pixels = bounded_texture(pano[top:bottom, left:right])
    return dict(image=jpeg(pixels), haov=haov, vaov=vaov, vOffset=offset,
                width=pixels.shape[1], height=pixels.shape[0], method="opencv"), indices, ""


def process_station(name, files, spherical=False):
    if not 1 <= len(files) <= MAX_STATION_PHOTOS:
        raise ValueError(f"每站请选择 1–{MAX_STATION_PHOTOS} 张照片")
    if spherical:
        if len(files) != 1:
            raise ValueError("完整球面模式每站只收一张 2:1 球面照片")
        filename, img = files[0]
        thumb = cv2.resize(img, (min(1400, img.shape[1]), min(700, img.shape[0])), interpolation=cv2.INTER_AREA)
        return dict(name=name, originals=[{"name": filename, "image": jpeg(thumb)}], kind="panorama",
                    matched=[0], reason="", panorama=dict(image=jpeg(img), haov=360, vaov=180, vOffset=0))
    items = []
    for filename, img in files:
        if len(files) > 8 and max(img.shape[:2]) > 960:
            h, w = img.shape[:2]
            scale = 960 / max(h, w)
            img = cv2.resize(img, (round(w*scale), round(h*scale)), interpolation=cv2.INTER_AREA)
        items.append({"name": filename, "image": jpeg(img)})
    result = dict(name=name, originals=items, matched=[], panorama=None)
    unique = {}
    for i, (_, image) in enumerate(files):
        key = (image.shape, hashlib.sha256(memoryview(np.ascontiguousarray(image))).digest())
        unique.setdefault(key, i)
    indices = list(unique.values())
    if len(indices) == 1:
        reason = "这站只有一张独立照片，保留原图视野。"
        return dict(result, kind="photo" if len(files) == 1 else "photos", reason=reason,
                    followup="站回原拍摄位置，向左或向右补一张，让两张都拍到同一个门框或家具。")
    diagnostics = {}
    images = [files[i][1] for i in indices]
    retry_budget = False
    try:
        panorama, matched, reason = stitch(images)
    except ProjectionBudgetExceeded as e:
        panorama, matched, reason = None, [], str(e)
        retry_budget = 4 <= len(images) <= 8 and any(max(img.shape[:2]) > 1400 for img in images)
    except (cv2.error, ValueError) as e:
        panorama, matched = None, []
        reason = str(e) if isinstance(e, ValueError) else "这组照片未能稳定拼接。"
    diagnostics["opencvMatched"] = len(matched)
    if reason:
        diagnostics["opencvNote"] = reason
    supplement = "如需扩大视野，站回这一点，向可拖画面的边缘外补拍，同时保留同一个门框或固定参照物。"
    if panorama:
        return dict(result, panorama=panorama, matched=[indices[i] for i in matched], reason=reason,
                    kind="panorama", geometry=dict(diagnostics, state="coverage-limited"), followup=supplement)

    from recovery import recover

    ordered = [unique[key] for key in sorted(unique)]
    try:
        panorama, matched = recover([files[i][1] for i in ordered], report=diagnostics)
    except (cv2.error, np.linalg.LinAlgError, ValueError):
        panorama, matched = None, []
    if retry_budget and (panorama is None or panorama["haov"] < 360):
        # Retry the same inputs under the existing raster limits; keep the stronger verified coverage.
        scaled = []
        for img in images:
            h, w = img.shape[:2]
            ratio = min(1, 1400 / max(h, w))
            scaled.append(img if ratio == 1 else cv2.resize(img, (round(w*ratio), round(h*ratio)), interpolation=cv2.INTER_AREA))
        try:
            candidate, used, _ = stitch(scaled)
        except (cv2.error, ValueError):
            candidate, used = None, []
        if candidate and (panorama is None or (candidate["haov"] >= panorama["haov"]
                                                and candidate["vaov"] >= panorama["vaov"])):
            candidate["method"] = "opencv-budget-retry"
            diagnostics = dict(opencvMatched=len(used), opencvNote=reason, state="coverage-limited")
            panorama, matched, ordered = candidate, used, indices
    result["geometry"] = diagnostics
    if panorama:
        angle = panorama["haov"]
        if angle == 360:
            offset, half = panorama["vOffset"], panorama["vaov"] / 2
            reason = f"水平 360° 实拍覆盖，俯视至 {offset-half:.0f}°、仰视至 {offset+half:.0f}°。"
            followup = ""
        else:
            reason = f"局部实拍，左右视野约 {round(angle)}°。"
            if diagnostics.get("state") == "closure-unverified":
                reason += "整圈接缝尚未确认。"
            followup = supplement
        return dict(result, panorama=panorama, matched=[ordered[i] for i in matched],
                    reason=reason, kind="panorama", followup=followup)
    if diagnostics.get("state") == "pair-alignment-unverified":
        return dict(result, kind="photos", reason="找到了部分共同画面，但重叠区无法可靠对齐，已保留原照。",
                    followup="如果拍摄时挪了位置，请把两张分站。要拼成同一站，请站在同一点转身拍摄，"
                             "让相邻照片都拍到同一处门框、柱子或栏杆。")
    return dict(result, kind="photos", reason=reason,
                followup="如果照片是同一位置拍的，补一张能同时拍到相邻照片边缘参照物的过渡照。"
                         "如果来自不同位置，请分站上传。")
