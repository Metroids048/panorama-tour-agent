"""Recover a continuous photographed view after the ordinary stitch fails."""
import itertools
import math

import cv2
import numpy as np

from align import bundle, rays
from engine import bounded_texture, covered_rectangle, jpeg
from guided import render


def sift_features(images, enhance, dense=False):
    threshold = (.018 if enhance else .024) if dense else (.024 if enhance else .032)
    detector = cv2.SIFT_create(nfeatures=2000 if dense else 1000, contrastThreshold=threshold)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(6, 6)) if enhance else None
    result = []
    for image in images:
        height, width = image.shape[:2]
        scale = min(1, (1100 if dense else 850) / max(height, width))
        gray = cv2.cvtColor(cv2.resize(image, (round(width * scale), round(height * scale)),
                                      interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        points, descriptors = detector.detectAndCompute(clahe.apply(gray) if clahe else gray, None)
        result.append((np.float32([point.pt for point in points]), descriptors, gray.shape[::-1]))
    return result


def sift_pair(first, second):
    points, descriptors, size = first
    other_points, other_descriptors, other_size = second
    if descriptors is None or other_descriptors is None or min(len(descriptors), len(other_descriptors)) < 12:
        return None
    matcher = cv2.BFMatcher()

    def matches(a, b):
        return {m.queryIdx: m.trainIdx for m, n in matcher.knnMatch(a, b, k=2)
                if m.distance < .82 * n.distance}

    forward = matches(descriptors, other_descriptors)
    backward = matches(other_descriptors, descriptors)
    mutual = [(i, j) for i, j in forward.items() if backward.get(j) == i]
    if len(mutual) < 10:
        return None
    i, j = np.asarray(mutual).T
    return geometric_pair(points[i], other_points[j], size, other_size)


def geometric_pair(x, y, size, other_size):
    if len(x) < 10:
        return None
    _, mask = cv2.findHomography(x, y, cv2.USAC_MAGSAC, max(1.5, max(*size, *other_size) * .004))
    if mask is None:
        return None
    keep = mask.ravel() > 0
    if keep.sum() < 10 or keep.mean() < .3:
        return None
    x, y = x[keep], y[keep]
    spread = min(cv2.contourArea(cv2.convexHull(points)) / math.prod(shape)
                 for points, shape in ((x, size), (y, other_size)))
    if spread < .012:
        return None
    return ((x - np.asarray(size) / 2) / max(size),
            (y - np.asarray(other_size) / 2) / max(other_size), spread)


def relative_rotation(x, y, focal):
    def unit(coords):
        rays = np.column_stack((coords[:, 0], -coords[:, 1], np.full(len(coords), focal)))
        return rays / np.linalg.norm(rays, axis=1, keepdims=True)

    def rotation(first, second):
        u, _, vt = np.linalg.svd(first.T @ second)
        u[:, -1] *= np.linalg.det(u @ vt)
        return u @ vt

    x, y = unit(x), unit(y)

    def errors(matrix):
        return np.rad2deg(np.arccos(np.clip(((y @ matrix.T) * x).sum(1), -1, 1)))

    keep = errors(rotation(x, y)) < 2
    if keep.sum() < 10 or keep.mean() < .55:
        return None
    matrix = rotation(x[keep], y[keep])
    residual = errors(matrix)
    median = float(np.median(residual))
    if median > 1.35:
        return None
    return matrix, median, int((residual < 2).sum()), float((residual < 2).mean())


def pose_tree(edges, count):
    parents = list(range(count))
    neighbors = [[] for _ in parents]

    def root(i):
        while parents[i] != i:
            i = parents[i]
        return i

    for _, i, j, matrix in sorted(edges, key=lambda edge: -edge[0]):
        a, b = root(i), root(j)
        if a != b:
            parents[b] = a
            neighbors[i].append((j, matrix))
            neighbors[j].append((i, matrix.T))
    components = {}
    for i in range(count):
        components.setdefault(root(i), []).append(i)
    component = max(components.values(), key=len)
    poses = {component[0]: np.eye(3)}
    pending = list(poses)
    while pending:
        i = pending.pop()
        for j, matrix in neighbors[i]:
            if j not in poses:
                poses[j] = poses[i] @ matrix
                pending.append(j)
    return component, poses


def rotation_difference(first, second):
    trace = np.trace(first.T @ second)
    return float(np.rad2deg(np.arccos(np.clip((trace - 1) / 2, -1, 1))))


def overlap_edges(pairs, images, component, lenses):
    lookup = {index: i for i, index in enumerate(component)}

    def plane(points, i):
        h, w = images[i].shape[:2]
        fx, fy = lenses if w > h else lenses[::-1]
        return points * [max(w, h) / (w * fx), -max(w, h) / (h * fy)]

    return [(lookup[i], lookup[j], plane(x, i), plane(y, j))
            for (i, j), (x, y, _) in pairs.items() if i in lookup and j in lookup]


def verified_loop(edges, matrices, focal_ratio):
    """Check that verified overlaps wind around the horizon, beyond a spanning tree."""
    component = range(len(matrices))
    neighbors = [[] for _ in component]
    headings = [math.degrees(math.atan2(m[0, 2], m[2, 2])) for m in matrices]
    for i, j, x, y in edges:
        first = rays(x, 1 / focal_ratio, matrices[i])[0]
        second = rays(y, 1 / focal_ratio, matrices[j])[0]
        residual = np.rad2deg(2 * np.arcsin(np.clip(np.linalg.norm(first - second, axis=1) / 2, 0, 1)))
        if np.median(residual) <= .85 and np.quantile(residual, .90) <= 2:
            neighbors[i].append(j)
            neighbors[j].append(i)

    seen = {}
    for start in component:
        if start in seen:
            continue
        seen[start] = headings[start]
        pending = [start]
        while pending:
            i = pending.pop()
            for j in neighbors[i]:
                delta = (headings[j] - headings[i] + 180) % 360 - 180
                heading = seen[i] + delta
                if j not in seen:
                    seen[j] = heading
                    pending.append(j)
                elif abs(seen[j] - heading) > 180:
                    return True
    return False


def check_pair_projection(images, lenses, matrices, frame_lenses):
    """Verify a two-frame fallback using corners independent of its SIFT fit."""
    width = 1800
    views = [render([im], lenses, [matrix], width=width, frame_lenses=[lens])
             for im, matrix, lens in zip(images, matrices, frame_lenses)]
    overlap = np.uint8((views[0][1] > 0) & (views[1][1] > 0))
    x, y, w, h = cv2.boundingRect(overlap)
    area = np.s_[y:y+h, x:x+w]
    mask = cv2.erode(overlap[area], np.ones((17, 17), np.uint8),
                     borderType=cv2.BORDER_CONSTANT, borderValue=0) if w and h else overlap[area]
    stats = dict(corners=0, tracked=0, aligned=0)
    if np.count_nonzero(mask) < 240:
        return False, stats

    def gray(im):
        g = cv2.cvtColor(im[area], cv2.COLOR_BGR2GRAY)
        low, high = np.quantile(g[mask > 0], [.1, .9])
        # Exposure or contrast differences are not a geometric mismatch.
        return ((g.astype(np.float32) - low) * (190 / max(24, high-low)) + 30).clip(0, 255).astype(np.uint8)

    a, b = [gray(im) for im, _ in views]
    points = cv2.goodFeaturesToTrack(a, 400, .012, 7, mask=mask)
    if points is None:
        return False, stats
    options = dict(winSize=(21, 21), maxLevel=3,
                   criteria=(cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, 25, .03))
    moved, status, error = cv2.calcOpticalFlowPyrLK(a, b, points, None, **options)
    reverse, back, _ = cv2.calcOpticalFlowPyrLK(b, a, moved, None, **options)
    p, q = points[:, 0], moved[:, 0]
    inside = np.isfinite(q).all(axis=1) & (q >= 0).all(axis=1) & (q < [w, h]).all(axis=1)
    qx, qy = np.rint(q[inside]).astype(int).T
    inside[inside] = mask[qy.clip(0, h-1), qx.clip(0, w-1)] > 0
    valid = ((status.ravel() > 0) & (back.ravel() > 0) & inside &
             (np.linalg.norm(p - reverse[:, 0], axis=1) < 1.25) & (error.ravel() < 22))
    degrees = np.linalg.norm(p[valid] - q[valid], axis=1) * 360 / width
    aligned = int((degrees <= 1.2).sum())
    stats.update(corners=len(points), tracked=len(degrees), aligned=aligned)
    if len(degrees):
        stats["p90Degrees"] = round(float(np.quantile(degrees, .9)), 2)
    return aligned >= max(12, .12 * len(points), .75 * len(degrees)), stats


def recover(images, pairs=None, *, report=None):
    """Return (panorama, indices) or (None, []) if no usable coverage survives."""
    if pairs is not None:
        return project(images, pairs, report)
    pairs = {}
    candidate, indices = None, []
    diagnostics = {}
    for dense in (False, True) if len(images) >= 10 else (False,):
        for enhance in (False, True):
            features = sift_features(images, enhance, dense)
            for i, j in itertools.combinations(range(len(images)), 2):
                if (i, j) not in pairs:
                    match = sift_pair(features[i], features[j])
                    if match is not None:
                        pairs[i, j] = match
        attempt = {}
        view, used = project(images, pairs, attempt)
        if (not diagnostics or (not candidate and attempt.get("state") == "pair-alignment-unverified") or
                (view and (not candidate or (view["haov"] == 360, view["haov"] * view["vaov"]) >
                           (candidate["haov"] == 360, candidate["haov"] * candidate["vaov"])))):
            candidate, indices, diagnostics = view, used, attempt
            diagnostics["denseFeatures"] = dense
        if candidate and candidate["haov"] == 360:
            break
    if report is not None:
        report.update(diagnostics)
    return candidate, indices


def project(images, pairs, report, *, pair_retries=2):
    best = None
    for focal in (.54, .63, .72, .81, .9, 1., 1.13, 1.25, 1.4, 1.55):
        edges = []
        for (i, j), (x, y, spread) in pairs.items():
            fitted = relative_rotation(x, y, focal)
            if fitted:
                matrix, error, count, fraction = fitted
                weight = math.log1p(count) * math.sqrt(spread) * fraction / (.2 + error)
                edges.append((weight, i, j, matrix))
        component, poses = pose_tree(edges, len(images))
        if len(component) < 2:
            continue
        closure = [(e, rotation_difference(poses[e[1]] @ e[3], poses[e[2]]))
                   for e in edges if e[1] in poses and e[2] in poses]
        if sum(error > 8 for _, error in closure) > len(closure) / 5:
            continue
        coherent = [edge for edge, error in closure if error < 5]
        component, poses = pose_tree(coherent, len(images))
        score = (len(component), sum(edge[0] for edge in coherent) /
                 (1 + np.median([error for _, error in closure]) * .3))
        if best is None or score > best[0]:
            best = score, focal, component, poses
    if report is not None:
        report.update(overlaps=len(pairs), state="pose-unreliable" if pairs else "no-overlap")
    if best is None:
        return None, []
    _, focal, component, poses = best
    selected = [images[i] for i in component]
    aspect = float(np.median([max(im.shape[:2]) / min(im.shape[:2]) for im in selected]))
    lenses = (focal, focal * aspect)
    matrices = [poses[i] for i in component]
    edges = overlap_edges(pairs, images, component, lenses)
    frame_lenses = None
    if len(component) == 2:
        # The initial rotation assumes one focal length per longest image edge.
        # Keep square pixels for each source, even if the photos have different aspect ratios.
        x, y, _ = pairs[tuple(component)]
        xy, yx = [p / [focal, -focal] for p in (x, y)]
        fitted, matrices, alignment = bundle((focal, focal), matrices, [(0, 1, xy, yx)], min_edges=1)
        frame_lenses = [(fitted[0] * max(h, w) / w, fitted[0] * max(h, w) / h)
                        for h, w in (im.shape[:2] for im in selected)]
        valid, audit = check_pair_projection(selected, lenses, matrices, frame_lenses)
        if report is not None:
            report.update(pairAlignment=audit, **alignment)
        if not valid:
            if report is not None:
                report["state"] = "pair-alignment-unverified"
            if pair_retries and len(pairs) > 1:
                remaining = {edge: points for edge, points in pairs.items() if edge != tuple(component)}
                alternative = {}
                view, used = project(images, remaining, alternative, pair_retries=pair_retries-1)
                if view:
                    if report is not None:
                        report.update(alternative, overlaps=len(pairs),
                                      skippedPairCount=alternative.get("skippedPairCount", 0) + 1)
                    return view, used
            return None, []
    else:
        fit_edges = []
        for i, j, x, y in edges:
            error = np.linalg.norm(rays(x, 1, matrices[i])[0] - rays(y, 1, matrices[j])[0], axis=1)
            # The tree accumulates pose drift. Include a strong closing edge even
            # if the seed is off, then judge it using the stricter post-fit gate.
            if np.median(error) > math.radians(25) or not relative_rotation(
                    pairs[component[i], component[j]][0], pairs[component[i], component[j]][1], focal):
                continue
            keep = np.linspace(0, len(x) - 1, min(48, len(x))).astype(int)
            fit_edges.append((i, j, x[keep], y[keep]))
        lenses, matrices, alignment = bundle(lenses, matrices, fit_edges)
    # Keep the texture's angular resolution proportional to the source pixels.
    # Limit the 2:1 render grid to keep the local CPU path and memory bounded.
    width = min(4800, max(1440, 8 * round(max(max(im.shape[:2]) for im in selected) / 4)))
    image, coverage = render(selected, lenses, matrices, width=width, frame_lenses=frame_lenses)
    height, width = coverage.shape
    complete = (coverage > 0).all(axis=1)
    top = bottom = height // 2
    while top > 0 and complete[top-1]:
        top -= 1
    while bottom < height and complete[bottom]:
        bottom += 1
    band = (bottom-top)*180/height
    closed = band >= 22 and verified_loop(edges, matrices, lenses[0] / focal)
    if report is not None:
        report.update(state="closed" if closed else "closure-unverified" if band >= 22 else "coverage-limited",
                      estimatedFrames=len(component), fullBand=round(band, 2),
                      sphereCoverage=round(float((coverage > 0).mean() * 100), 2), **alignment)
    if closed:
        pixels = bounded_texture(image[top:bottom])
        return dict(image=jpeg(pixels), haov=360, vaov=band, width=pixels.shape[1],
                    height=pixels.shape[0], method="photo-loop",
                    vOffset=90-(top+bottom)*90/height), component

    # If projection alone covers the circle but the overlaps cannot prove a loop,
    # keep a bounded window and leave the unverified return seam out of the viewer.
    crop_width = width if band < 22 else 4 * (width * 330 // 360 // 4)
    best_crop = None
    for shift in (0, width // 4, width // 2, 3 * width // 4):
        try:
            left, top, right, bottom = covered_rectangle(np.roll(coverage > 0, shift, axis=1)[:, :crop_width])
        except ValueError:
            continue
        haov, vaov = (right - left) * 360 / width, (bottom - top) * 180 / height
        if not (24 <= haov < 360 and vaov >= 22):
            continue
        area = (right - left) * (bottom - top)
        if best_crop is None or area > best_crop[0]:
            best_crop = area, shift, left, top, right, bottom, haov, vaov
    if best_crop is None:
        return None, []
    _, shift, left, top, right, bottom, haov, vaov = best_crop
    pixels = np.roll(image, shift, axis=1)[top:bottom, left:right]
    if width > 1440 and pixels.shape[0] * pixels.shape[1] <= 1_750_000:
        # Sample the original photos again, only inside the confirmed viewport.
        # An inset avoids including new pixels right on the old coverage edge.
        window = (2*(left-shift)+2, 2*top+2, 2*(right-shift)-2, 2*bottom-2)
        detail, mask = render(selected, lenses, matrices, width=2*width, window=window, frame_lenses=frame_lenses)
        if (mask > 0).all():
            pixels = detail
            haov -= 720 / width
            vaov -= 720 / width
    pixels = bounded_texture(pixels)
    return dict(image=jpeg(pixels), haov=haov, vaov=vaov, width=pixels.shape[1], height=pixels.shape[0],
                method="matched-subset",
                vOffset=90 - (top + bottom) * 90 / height), component
