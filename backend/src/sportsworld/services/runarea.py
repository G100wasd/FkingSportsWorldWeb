"""跑步区域编排：围栏 + 实时打卡点 -> 确定本次跑步的体育场。

服务端按锚点就近分配跑步区；打卡点落在哪个围栏，
哪个围栏就是本次跑步的有效区域（田径场）。
"""

from ..domain.geom import stadium_fence_index
from . import geo


def fetch_run_area(sess, lat: float, lon: float, ts_ms: int | None = None) -> dict:
    """拉围栏 + 锚点附近实时打卡点，返回跑步区域上下文。

    返回:
        polygons:     全部围栏多边形 [[(lat,lon),...],...]
        raws:         围栏原始对象（元数据）
        run_area_id:  服务端 runAreaId（-1=未配置区域）
        points:       打卡点原始记录（pointName/isFixed/lat/lon/glat/glon）
        points_bd:    [(lat,lon),...] BD-09 拟合输入
        stadium_idx:  跑步区域在多边形中的索引
        stadium_ring: 跑步区域多边形（打卡点命中数最多的围栏）
        ref_center:   跑步区域质心（拟合初始化参考中心，不固定结果）
    """
    fence = geo.fetch_geo_fence(sess)
    polys = fence["polygons"]
    if not polys:
        raise RuntimeError("服务端未返回围栏多边形，无法确定跑步区域")

    pts = geo.fetch_points(sess, lat, lon, fence["run_area_id"], ts_ms=ts_ms)
    bd = geo.points_bd(pts)
    if not bd:
        raise RuntimeError("点位接口未返回有效坐标")

    idx = stadium_fence_index(polys, bd)
    if idx is None:
        raise RuntimeError("打卡点不在任何围栏内，无法确定跑步区域")
    ring = polys[idx]
    ref = (sum(p[0] for p in ring) / len(ring),
           sum(p[1] for p in ring) / len(ring))
    return {
        "polygons": polys,
        "raws": fence["raw"],
        "run_area_id": fence["run_area_id"],
        "points": pts,
        "points_bd": bd,
        "stadium_idx": idx,
        "stadium_ring": ring,
        "ref_center": ref,
    }
