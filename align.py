"""Refine recorded poses with overlapping photo features under a rotation model."""
import math

import cv2
import numpy as np


def cross_jac(rays):
    x, y, z = rays.T
    zero = np.zeros_like(x)
    return np.stack((zero,z,-y,-z,zero,x,y,-x,zero), axis=1).reshape(-1,3,3)


def rays(xy, factor, matrix):
    x = np.column_stack((xy * factor, np.ones(len(xy))))
    norm = np.linalg.norm(x, axis=1, keepdims=True)
    direction = x / norm
    # Derivative of the unit ray with respect to log inverse focal length.
    deriv = (direction * direction[:,2:3] - np.array([0,0,1])) * direction[:,2:3]
    return direction @ matrix.T, deriv @ matrix.T


def refine(images, lenses, matrices):
    n = len(images)
    orientations = np.stack(matrices)
    direction = orientations[:,:,2]
    neighbors = direction @ direction.T
    detector = cv2.SIFT_create(nfeatures=500)
    features = []
    for im in images:
        h, w = im.shape[:2]
        fx, fy = lenses if w > h else lenses[::-1]
        scale = min(1, 640/max(h, w))
        gray = cv2.cvtColor(cv2.resize(im, (round(w*scale), round(h*scale)), interpolation=cv2.INTER_AREA),cv2.COLOR_BGR2GRAY)
        k, desc = detector.detectAndCompute(gray, None)
        hh, ww = gray.shape
        coords = np.array([((p.pt[0]+.5)/ww-.5, .5-(p.pt[1]+.5)/hh) for p in k]).reshape(-1,2)
        features.append((coords / [fx,fy], desc))
    matcher = cv2.BFMatcher()
    pairs = {(min(i,j),max(i,j)) for i in range(n) for j in np.argsort(-neighbors[i])[1:11]
             if i != j and neighbors[i,j] > math.cos(math.radians(49))}
    edges = []
    for i,j in sorted(pairs):
        x, d = features[i]
        y, e = features[j]
        if d is None or e is None or len(e) < 2:
            continue
        good = [a for match in matcher.knnMatch(d,e,k=2) if len(match)==2
                for a,b in [match] if a.distance < .74*b.distance]
        if len(good) < 12:
            continue
        p = x[[m.queryIdx for m in good]]
        q = y[[m.trainIdx for m in good]]
        a,_ = rays(p,1,orientations[i])
        b,_ = rays(q,1,orientations[j])
        plausible = np.linalg.norm(a-b,axis=1) < .38
        if plausible.sum() < 12:
            continue
        p, q = p[plausible], q[plausible]
        _, mask = cv2.findHomography(p,q,cv2.RANSAC,.013)
        if mask is None or mask.sum() < 10:
            continue
        ids = np.flatnonzero(mask)
        ids = ids[np.linspace(0,len(ids)-1,min(42,len(ids))).astype(int)]
        edges.append((i,j,p[ids],q[ids]))
    return bundle(lenses, matrices, edges)


def bundle(lenses, matrices, edges, *, min_edges=6):
    n = len(matrices)
    orientations = np.stack(matrices)

    def errors(m, focal):
        return np.concatenate([np.linalg.norm(rays(p,focal,m[i])[0]-rays(q,focal,m[j])[0],axis=1)
                               for i,j,p,q in edges])
    connected = {i for a,b,_,_ in edges for i in (a,b)}
    if len(connected) < n*.45 or len(edges) < min_edges:
        return lenses, matrices, dict(alignedFrames=len(connected), featurePairs=len(edges))
    size = n*3+1
    factor = 1.
    before = float(np.median(errors(orientations, factor)))
    for _ in range(12):
        lhs = np.eye(size)*.035
        lhs[-1,-1] = 1.
        rhs = np.zeros(size)
        lhs[:3,:3] += np.eye(3)*12  # fix the arbitrary global heading
        for i,j,p,q in edges:
            a, da = rays(p, factor, orientations[i])
            b, db = rays(q, factor, orientations[j])
            diff = a-b
            weights = np.sqrt(np.minimum(1,.015 / np.maximum(np.linalg.norm(diff,axis=1),1e-6)))
            design = np.concatenate((cross_jac(a),-cross_jac(b),(da-db)[:,:,None]),axis=2)
            mat = (design*weights[:,None,None]).reshape(-1,7)
            ix = [*range(3*i,3*i+3),*range(3*j,3*j+3),size-1]
            lhs[np.ix_(ix,ix)] += mat.T @ mat
            rhs[ix] -= mat.T @ (diff*weights[:,None]).reshape(-1)
        change = np.linalg.solve(lhs,rhs)
        for i in range(n):
            vec = change[i*3:i*3+3].clip(-.12,.12)
            orientations[i] = cv2.Rodrigues(vec)[0] @ orientations[i]
        factor = float(np.clip(factor*np.exp(change[-1]),.72,1.3))
        if np.max(np.abs(change)) < .0003:
            break
    after = float(np.median(errors(orientations, factor)))
    return tuple(v/factor for v in lenses), orientations, dict(
        alignedFrames=len(connected), featurePairs=len(edges), focalRatio=round(1/factor,3),
        overlapErrorBefore=round(math.degrees(before),2), overlapErrorAfter=round(math.degrees(after),2))
