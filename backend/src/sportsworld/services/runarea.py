"""跑步区域编排：围栏列表 + 实时打卡点 -> 确定本次跑步的体育场。

服务端按区域（fence 级 runAreaId）分配打卡点；锚点仅做就近参考。
与原仓库 campus.rs 对应：每个围栏自带 runAreaId，选择哪个围栏
就拉哪个区域的点。
"""

from ..domain.geom import in_ring, stadium_fence_index
from . import geo, policy


def _to_int(v, default=-1):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _ring_center(ring):
    n = len(ring) or 1
    return sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n


def _ring_area_deg(ring):
    """鞋带面积（度²），仅用于比较大小。"""
    return abs(sum(
        ring[i][1] * ring[(i + 1) % len(ring)][0]
        - ring[(i + 1) % len(ring)][1] * ring[i][0]
        for i in range(len(ring)))) / 2


def fetch_fences(sess) -> dict:
    """拉围栏并整理区域列表。返回:
        polygons:    [[(lat,lon),...],...]
        areas:       [{idx, name, run_area_id, campus_id}]
        run_area_id: 顶层 runAreaId（-1=未配置）
        raws:        围栏原始对象
    """
    fence = geo.fetch_geo_fence(sess)
    polys = fence["polygons"]
    if not polys:
        raise RuntimeError("服务端未返回围栏多边形")
    areas = []
    for i, raw in enumerate(fence["raw"]):
        if i >= len(polys):
            break
        areas.append({
            "idx": i,
            "name": (raw.get("campusName") or raw.get("fenceName")
                     or raw.get("name") or f"围栏{i + 1}"),
            "run_area_id": _to_int(raw.get("runAreaId")),
            "campus_id": raw.get("campusId"),
        })
    return {"polygons": polys, "areas": areas,
            "run_area_id": fence["run_area_id"], "raws": fence["raw"],
            "geo_fences": fence.get("geo_fences", [])}


def pick_area(fence_bundle: dict, lat: float, lon: float, area_idx=None):
    """确定拉点区域：显式 idx > 锚点所在围栏 > 顶层 runAreaId。

    返回 (area_idx, run_area_id)；run_area_id 为 None 表示请求不带该字段。
    """
    polys = fence_bundle["polygons"]
    if area_idx is None:
        hits = [a["idx"] for a in fence_bundle["areas"]
                if in_ring(polys[a["idx"]], lat, lon)]
        if hits:
            # 围栏嵌套时取最小者：体育场内部 -> 体育场而非全校
            area_idx = min(hits, key=lambda i: _ring_area_deg(polys[i]))
    rid = fence_bundle["areas"][area_idx]["run_area_id"] if area_idx is not None else -1
    if rid < 0:
        rid = fence_bundle["run_area_id"]
    return area_idx, (rid if rid >= 0 else None)


def fetch_area_points(sess, fence_bundle: dict, lat: float, lon: float,
                      area_idx=None, ts_ms: int | None = None) -> dict:
    """拉指定/锚点所在区域的实时打卡点（服务端限流 ~5分钟3次）。

    与 _fence_test 顺序一致：先 runModePolicy(1) 申请计分跑（可能激活
    场地跑会话，影响点位池），失败容忍后再拉点位。
    """
    polys = fence_bundle["polygons"]
    area_idx, rid = pick_area(fence_bundle, lat, lon, area_idx)

    scoring = None
    try:
        biz = policy.fetch_policy(sess, run_mode=1, ts_ms=ts_ms)
        summ = policy.policy_summary(biz)
        scoring = {"ok": True, "minDistance": summ["minDistance"],
                   "validTime": summ["validTime"], "policy": summ["policy"],
                   "must_points": summ["must_points"]}
        print(f"[scoring] 开启成功 minDistance={summ['minDistance']} "
              f"policy={summ['policy']} must={len(summ['must_points'])}")
    except RuntimeError as e:
        scoring = {"ok": False, "error": str(e)}
        print(f"[scoring] 开启被拒绝: {e}")

    pts = geo.fetch_points(sess, lat, lon, rid, ts_ms=ts_ms)
    bd = geo.points_bd(pts)
    if not bd:
        raise RuntimeError("点位接口未返回有效坐标")
    stadium_idx = stadium_fence_index(polys, bd)
    if stadium_idx is None:
        stadium_idx = area_idx
    if stadium_idx is None:
        raise RuntimeError("打卡点不在任何围栏内，无法确定跑步区域")
    ring = polys[stadium_idx]
    return {
        "polygons": polys,
        "raws": fence_bundle["raws"],
        "areas": fence_bundle["areas"],
        "area_idx": area_idx,
        "area_run_area_id": rid,
        "stadium_idx": stadium_idx,
        "stadium_ring": ring,
        "ref_center": _ring_center(ring),
        "points": pts,
        "points_bd": bd,
        "scoring": scoring,
        # 五点 wrapper 元数据（wire.five_point_wrapper 用）
        "meta_run_area_id": fence_bundle.get("run_area_id", -1),
        "geo_fences": fence_bundle.get("geo_fences") or [],
        "freedom_show_fence": bool(fence_bundle.get("geo_fences")),
    }
