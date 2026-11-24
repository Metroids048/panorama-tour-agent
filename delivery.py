"""Classify offline tour readiness from the exported views and links."""
from copy import deepcopy


def bind_request(manifest, previous):
    """Keep the requested outcome across the first-look and revision shortcuts."""
    original = (previous or {}).get("request")
    if not original and previous and previous.get("purpose"):
        original = {"purpose": previous["purpose"], "mode": "venue" if "venue" in previous else "route"}
    if original:
        if manifest.get("request", original) != original:
            raise ValueError("不能在续改中改写原始目标；请另建任务处理新的用途。")
        if original["mode"] == "route" and previous.get("visitor_route") and not manifest.get("visitor_route"):
            raise ValueError("原始目标仍是实景导览，不能静默改成照片浏览。")
        manifest["request"] = deepcopy(original)
    elif manifest.get("purpose"):
        manifest["request"] = {"purpose": manifest["purpose"], "mode": "venue" if "venue" in manifest else "route"}
    route = (original or {}).get("required_route")
    if route is not None and manifest.get("required_route", route) != route:
        raise ValueError("不能在续改中改写用户指定站序。")
    route = manifest.get("required_route", route)
    if route is not None:
        if (not isinstance(route, list) or len(route) < 2 or any(not isinstance(s, str) for s in route)
                or len(set(route)) != len(route) or "venue" in manifest):
            raise ValueError("用户指定站序须为互不重复的路线站点 ID。")
        manifest["required_route"] = route
        request = manifest.setdefault("request", {"purpose": "", "mode": "route"})
        request["required_route"] = deepcopy(route)


def requested_route_gap(tour, route):
    """Check the real opening and consecutive arrows for an explicitly requested route."""
    if not route:
        return ""
    stations = tour["stations"]
    order = [s["id"] for s in stations]
    if (tour["entry"] is None or order[tour["entry"]] != route[0]
            or [station for station in order if station in route] != route):
        return "开场或实际站序与用户指定路线不一致。"
    for a, b in zip(route, route[1:]):
        for source, target in ((a, b),) if tour.get("forward_only") else ((a, b), (b, a)):
            if not any(link["to"] == order.index(target) and "yaw" in link and not link.get("cut")
                       for link in stations[order.index(source)]["links"]):
                return f"用户指定路线缺少实景方向 {source} → {target}。"
    return ""


def protect_routes(previous, manifest, old_edges, new_edges):
    """A local edit must not delete unrelated, previously confirmed directions."""
    if not previous:
        return
    before = {s["id"]: s for s in previous["stations"]}
    after = {s["id"]: s for s in manifest["stations"]}
    changed = {key for key in before.keys() & after.keys()
               if any(before[key].get(k) != after[key].get(k)
                      for k in ("files", "capture")) or
               any(bool(before[key].get(k)) != bool(after[key].get(k))
                   for k in ("spherical", "use_originals"))}
    confirmed = {(e["from"], e["to"]) for e in new_edges
                 if e["status"] == "confirmed" and not e.get("photo_only")}
    explicit = {(e.get("from_id"), e.get("to_id")) for e in manifest.get("route_changes", [])
                if isinstance(e, dict) and isinstance(e.get("reason"), str) and e["reason"].strip()}
    for edge in old_edges:
        pair = edge["from"], edge["to"]
        if (edge["status"] == "confirmed" and not edge.get("photo_only") and
                not changed.intersection(pair) and pair not in confirmed and pair not in explicit):
            raise ValueError(f"续改丢失已确认实景通道 {pair[0]} → {pair[1]}；保留原箭头，或逐方向说明用户要求的删除。")


def fulfillment(manifest, tour, grade):
    """Measure the frozen viewer graph, independently of caller flags and labels."""
    stations = tour["stations"]
    graph = {i: {e["to"] for e in s.get("links", [])
                 if s.get("panorama") and not e.get("cut") and "photo" not in e and
                 isinstance(e.get("yaw"), (int, float)) and isinstance(e.get("pitch"), (int, float))
                 and isinstance(e.get("to"), int) and 0 <= e["to"] < len(stations)}
             for i, s in enumerate(stations)}
    entry = tour.get("entry")
    reached = {entry} if isinstance(entry, int) and entry in graph else set()
    pending = list(reached)
    while pending:
        for target in graph[pending.pop()] - reached:
            reached.add(target)
            pending.append(target)
    arrows = sum(len(targets) for targets in graph.values())
    complete = bool(arrows and grade.get("panorama_ready") and grade.get("route_ready")
                    and len(reached) == len(stations) and not grade.get("blocked"))
    state = "first-look" if manifest.get("first_look") else "ready" if complete else "partial" if arrows else "view-only"
    out = {"state": state, "complete": complete, "arrows": arrows,
           "reachable": [s["id"] for i, s in enumerate(stations) if i in reached],
           "request": deepcopy(manifest.get("request") or {"purpose": manifest.get("purpose", ""),
                                                           "mode": "venue" if "venue" in manifest else "route"})}
    if "venue" not in tour and state == "partial":
        pairs = [(c["from"], c["to"]) for c in tour["route_connections"]] if "route_connections" in tour else [
            (i, i+1) for i in range(entry if isinstance(entry, int) else 0, len(stations) - 1)]
        for source, target in pairs:
            if source in reached and target not in graph[source]:
                out["first_gap"] = {"from_id": stations[source]["id"], "to_id": stations[target]["id"]}
                break
    return out


def download_scope(result, requested_kind):
    if not result["arrows"]:
        return "draft_download", "看景草稿"
    if not result["complete"]:
        return "download", "分层预览" if result["request"].get("mode") == "venue" else "局部实景试走"
    return "download", requested_kind


def quality(stations, links, *, reciprocal=True, minimum=2, required=None, entry=None):
    def wide(station):
        p = station.get("view") or {}
        return p.get("haov", 0) >= 180 and p.get("vaov", 0) >= 35

    def pixels(station):
        p = station["view"]
        return p.get("width", 0) >= 8*p["haov"] and p.get("height", 0) >= max(480, 8*p["vaov"])

    panorama = [s for s in stations if s.get("view")]
    network = required is not None
    required = set(required) if network else {(a["id"], b["id"]) for a, b in zip(stations, stations[1:])}
    if reciprocal:
        required |= {(b, a) for a, b in required}
    confirmed = {(e["from"], e["to"]) for e in links if e["status"] == "confirmed" and not e.get("photo_only")} & required
    cut = {(e["from"], e["to"]) for e in links if e["status"] == "cut"} & required
    reasons = []
    detail_limited = []
    for index, s in enumerate(stations):
        p = s.get("view")
        if not p:
            reasons.append(f'{s["name"]}：保留原照；从该点补拍带重叠的角度。')
        else:
            if not wide(s):
                reasons.append(f'{s["name"]}：视角不足 180°×35°；补拍相邻方向。')
            if not pixels(s):
                detail_limited.append(index)
                if (p["haov"], p["vaov"]) == (360, 180) and p.get("width") and p.get("height"):
                    reasons.append(f'{s["name"]}：整圈球面只有 {p["width"]}×{p["height"]}，放大容易模糊。'
                                   '请发送同站更清晰的 2:1 球面原件，建议 5760×2880 或以上。')
                else:
                    reasons.append(f'{s["name"]}：有效像素不足；用更清楚、有重叠的原片替换。')
    if len(stations) < minimum:
        reasons.append("只有一个拍摄点；加入另一个点位才能换站。" if minimum == 2
                       else f"实景点位不足；至少需要 {minimum} 个点位。")
    if network:
        ids = {s["id"] for s in stations}
        start = entry if entry is not None else stations[0]["id"]
        for reverse in ([False, True] if reciprocal else [False]):
            reached, pending = {start}, [start]
            while pending:
                current = pending.pop()
                for source, target in confirmed:
                    source, target = (target, source) if reverse else (source, target)
                    if source == current and target not in reached:
                        reached.add(target)
                        pending.append(target)
            if reached != ids:
                reasons.append("声明的通道必须覆盖全部站点，并能沿实景箭头返回入口。")
                break
    missing = len(required - confirmed - cut)
    if missing:
        reasons.append(f"有 {missing} 个方向尚未放置画面内箭头或切换按钮。")
    if not reasons:
        kind = "tour" if confirmed == required else "switch"
        label = "全景导览 · 实景箭头" if kind == "tour" else "多站全景路线预览"
        if kind == "switch":
            reasons.append(f"有 {len(required - confirmed)} 个方向仍需核对实景并放置全景画面内箭头。")
    elif len(stations) == 1:
        kind, label = "single", "单站环顾对照" if panorama else "单站原照对照"
    else:
        kind, label = "limited", "局部视野路线预览" if panorama else "原照路线对照"
    return dict(kind=kind, label=label, route_ready=kind == "tour", arrows=len(confirmed), cuts=len(cut - confirmed),
                required=len(required), reasons=reasons, detail_limited=detail_limited)


def check_delivery(manifest, grade, require_tour, require_panorama=False):
    if not (require_tour or require_panorama):
        return
    strict = (require_panorama or (manifest.get("fixed_route") or {}).get("require_panorama") is True
              or (manifest.get("venue") or {}).get("require_cross_floor") is True)
    if strict and not grade.get("panorama_ready"):
        reasons = grade.get("panorama_reasons") or grade.get("reasons")
        raise ValueError("全景成品未达标：" + (" ".join(reasons) if reasons else "请用当前全景门槛核对并导出新版。"))
    if not grade.get("route_ready"):
        raise ValueError("成品导览未达标：" + " ".join(grade.get("reasons") or ["请核对并导出新版。"]))
