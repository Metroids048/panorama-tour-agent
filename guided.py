"""Stitch a guided capture from perspective photos and recorded camera poses."""
import math

import cv2
import numpy as np

from engine import covered_rectangle, jpeg
from align import refine


def capture_spec(capture):
    try:
        focal, sensor_x, sensor_y = [float(capture[k]) for k in ("focalLength", "angleViewX", "angleViewY")]
        if not all(math.isfinite(v) and v > 0 for v in (focal, sensor_x, sensor_y)):
            raise ValueError()
        lenses = tuple(focal / v for v in sorted((sensor_x, sensor_y), reverse=True))
        if not all(.3 <= v <= 5 for v in lenses):
            raise ValueError()
        poses = {}
        for picture in capture["pictures"]:
            sensor = picture["sensors"]
            name = sensor["fileUri"]
            if not isinstance(name, str) or name in poses or "/" in name or "\\" in name:
                raise ValueError()
            pose = tuple(float(sensor["roll_pitch_yaw"][k]) for k in ("yaw", "pitch", "roll"))
            if not all(math.isfinite(v) and abs(v) <= 360 for v in pose) or abs(pose[1]) > 90:
                raise ValueError()
            poses[name] = pose
        if not 3 <= len(poses) <= 96:
            raise ValueError()
    except (TypeError, KeyError, ValueError, OverflowError) as e:
        raise ValueError("拍摄记录无效：需要 3–96 张照片及各张相机朝向") from e
    return lenses, poses


def camera_pose(yaw, pitch, roll):
    y, p, r = np.deg2rad([yaw, -pitch, roll])
    forward = np.array([np.cos(p) * np.sin(y), np.sin(p), np.cos(p) * np.cos(y)])
    right = np.array([np.cos(y), 0, -np.sin(y)])
    up = np.cross(forward, right)
    return np.column_stack((right * np.cos(r) + up * np.sin(r),
                            up * np.cos(r) - right * np.sin(r), forward))


def render(images, lenses, matrices, width=2880, window=None, *, frame_lenses=None, narrow_overlap=False):
    height = width // 2
    left, top, right, bottom = window if window is not None else (0, 0, width, height)
    yaw = ((np.arange(left, right, dtype=np.float32) + .5) / width * 2 - 1) * np.pi
    pitch = (.5 - (np.arange(top, bottom, dtype=np.float32) + .5) / height) * np.pi
    x = np.cos(pitch[:, None]) * np.sin(yaw)
    y = np.sin(pitch[:, None])
    z = np.cos(pitch[:, None]) * np.cos(yaw)
    pixels = np.zeros((bottom-top, right-left, 3), np.float32)
    weights = np.zeros(pixels.shape[:2], np.float32)
    counts = np.zeros(pixels.shape[:2], np.uint8)
    for i, (img, matrix) in enumerate(zip(images, matrices)):
        h, w = img.shape[:2]
        fx, fy = frame_lenses[i] if frame_lenses is not None else lenses if w > h else lenses[::-1]
        center = math.asin(float(np.clip(matrix[1, 2], -1, 1)))
        margin = math.atan(math.hypot(.5/fx, .5/fy))
        rows = np.flatnonzero(np.abs(pitch-center) < margin + .001)
        if not len(rows):
            continue
        band = slice(rows[0], rows[-1] + 1)
        def project(v):
            return x[band]*v[0] + y[band]*v[1] + z[band]*v[2]
        depth = project(matrix[:, 2])
        safe = np.maximum(depth, 1e-6)
        mapx = (project(matrix[:, 0]) * fx/safe + .5)*w-.5
        mapy = (.5 - project(matrix[:, 1]) * fy/safe)*h-.5
        edge_x = 1 - abs((mapx+.5)/w*2-1)
        edge_y = 1 - abs((mapy+.5)/h*2-1)
        valid = (depth > 0) & (mapx >= 1) & (mapx < w-2) & (mapy >= 1) & (mapy < h-2)
        if narrow_overlap:
            # Let the horizontal edge choose the source even near the top and bottom.
            edge = np.maximum(edge_x, .01) * np.maximum(edge_y, .01)
            blend = np.maximum(edge, .001)**10
        else:
            blend = np.maximum(np.minimum(edge_x, edge_y), .01)**5
        weight = np.where(valid, blend, 0).astype(np.float32)
        photo = cv2.remap(img, mapx.astype(np.float32), mapy.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        weights[band] += weight
        counts[band] += valid
        pixels[band] += photo * weight[:,:,None]
    result = (pixels / np.where(weights > 0, weights, 1)[:,:,None]).clip(0,255).astype(np.uint8)
    return result, counts


def process_guided(name, files, capture):
    lenses, poses = capture_spec(capture)
    width = capture.get("renderWidth", 2880)
    if type(width) is not int or width not in (2880, 3840):
        raise ValueError("拍摄记录的全景输出宽度须为 2880 或 3840")
    if len(files) != len(poses) or {p for p, _ in files} != set(poses):
        raise ValueError("上传的照片文件名必须和拍摄记录一一对应")
    images = [img for _, img in files]
    matrices = [camera_pose(*poses[p]) for p, _ in files]
    lenses, matrices, alignment = refine(images, lenses, matrices)
    result, counts = render(images, lenses, matrices, width=width,
                            narrow_overlap=capture.get("narrowOverlap") is True)
    h = result.shape[0]
    complete = (counts > 0).all(axis=1)
    top, bottom = h//2, h//2
    while top > 0 and complete[top-1]:
        top -= 1
    while bottom < h and complete[bottom]:
        bottom += 1
    band = (bottom-top)*180/h
    report = dict(coverage=round(float((counts>0).mean()*100),2),
                  pitchTop=round(90-top*180/h,2), pitchBottom=round(90-bottom*180/h,2),
                  horizon=bool(complete[h//2]), sourceCount=len(files), **alignment)
    originals = []
    for filename, img in files:
        h0, w0 = img.shape[:2]
        scale = min(1, 320/max(h0, w0))
        small = cv2.resize(img, (round(w0*scale), round(h0*scale)), interpolation=cv2.INTER_AREA)
        originals.append(dict(name=filename, image=jpeg(small)))
    if band < 35:
        width = result.shape[1]
        covered = counts > 0
        gap = np.flatnonzero(~covered[h//2])
        cut = int(gap[np.argmin(covered[:, gap].sum(axis=0))]) if len(gap) else 0
        try:
            left, top, right, bottom = covered_rectangle(np.roll(covered, -cut, axis=1))
        except ValueError:
            left = top = right = bottom = 0
        horizontal = (right-left)*360/width
        vertical = (bottom-top)*180/h
        followup = "回到这站原拍摄位置，沿可拖视野边缘补拍，和边缘已有照片保留重叠。"
        if horizontal < 30 or vertical < 25:
            return dict(name=name, originals=originals, matched=[], kind="photos", capture=report, panorama=None,
                        reason="这一站缺少连续实拍覆盖，保留原照。", followup=followup)
        columns = (np.arange(left, right) + cut) % width
        offset = 90-(top+bottom)*90/h
        center = ((cut+(left+right)/2)*360/width) % 360 - 180
        report.update(yawCenter=round(center, 4), pitchRange=round(vertical, 2),
                      pitchTop=round(90-top*180/h, 2), pitchBottom=round(90-bottom*180/h, 2))
        return dict(name=name, originals=originals, matched=list(range(len(files))), kind="panorama",
                    capture=report, reason=f"本点位可环顾实拍的 {horizontal:g}°；其余方向缺拍。",
                    followup=followup,
                    panorama=dict(image=jpeg(result[top:bottom][:, columns]), haov=horizontal,
                                  vaov=vertical, vOffset=offset))
    offset = 90-(top+bottom)*90/h
    report["pitchRange"] = round(band,2)
    reason = "水平 360° · 实拍范围" if band < 180 else "水平 360° · 垂直 180° · 实拍拼接"
    if band < 180:
        reason += f"（仰视至 {report['pitchTop']:g}°，俯视至 {report['pitchBottom']:g}°；未拍范围已限制）"
    return dict(name=name, originals=originals, matched=list(range(len(files))), kind="panorama",
                capture=report, reason=reason,
                panorama=dict(image=jpeg(result[top:bottom]), haov=360, vaov=band, vOffset=offset))
