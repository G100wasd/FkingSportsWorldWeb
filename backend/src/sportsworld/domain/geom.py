"""围栏几何与环工具（BD-09 经纬度 <-> 局部米制平面）。

坐标约定与 Rust src/track/geom.rs 一致：
    lat = c_lat + y / MET_LAT
    lon = c_lng + x / MET_LNG
即局部平面 x 轴朝东、y 轴朝北。
"""

import bisect
import math

MET_LAT = 111_132.0
MET_LNG = 86_600.0


def ll_to_xy(lat: float, lon: float, center: tuple[float, float]) -> tuple[float, float]:
    return (lon - center[1]) * MET_LNG, (lat - center[0]) * MET_LAT


def xy_to_ll(x: float, y: float, center: tuple[float, float]) -> tuple[float, float]:
    return center[0] + y / MET_LAT, center[1] + x / MET_LNG


def in_ring(ring, lat: float, lon: float) -> bool:
    """射线法判断 (lat,lon) 是否在经纬度多边形内。ring=[(lat,lon),...]"""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if (xi > lon) != (xj > lon) and lat < (yj - yi) * (lon - xi) / (xj - xi) + yi:
            inside = not inside
        j = i
    return inside


def seg_dist(px, py, ax, ay, bx, by) -> float:
    """点到线段距离。"""
    vx, vy = bx - ax, by - ay
    vv = vx * vx + vy * vy
    t = 0.0 if vv <= 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / vv))
    return math.hypot(px - ax - t * vx, py - ay - t * vy)


def poly_signed_dist(ring_xy, px, py) -> float:
    """点到多边形边界的带符号距离（m）：>0 场内，<=0 场外。ring_xy 为局部米制。"""
    inside = False
    j = len(ring_xy) - 1
    dmin = 1e18
    for i in range(len(ring_xy)):
        xi, yi = ring_xy[i]
        xj, yj = ring_xy[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        d = seg_dist(px, py, xi, yi, xj, yj)
        if d < dmin:
            dmin = d
        j = i
    return dmin if inside else -dmin


def convex_hull(pts):
    """单调链凸包，返回逆时针顶点。"""
    p = sorted(set((float(x), float(y)) for x, y in pts))
    if len(p) < 3:
        return p

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lo = []
    for q in p:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    up = []
    for q in reversed(p):
        while len(up) >= 2 and cross(up[-2], up[-1], q) <= 0:
            up.pop()
        up.append(q)
    return lo[:-1] + up[:-1]


def min_area_rect(hull):
    """旋转卡壳求最小面积外接矩形 -> (cx, cy, ang, L, W)，L >= W。"""
    best = None
    for i in range(len(hull)):
        x1, y1 = hull[i]
        x2, y2 = hull[(i + 1) % len(hull)]
        ang = math.atan2(y2 - y1, x2 - x1)
        ca, sa = math.cos(-ang), math.sin(-ang)
        xs = [x * ca - y * sa for x, y in hull]
        ys = [x * sa + y * ca for x, y in hull]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if best is None or area < best[0]:
            best = (area, ang, min(xs), max(xs), min(ys), max(ys))
    _, ang, xa, xb, ya, yb = best
    cxr, cyr = (xa + xb) / 2, (ya + yb) / 2
    ca, sa = math.cos(ang), math.sin(ang)
    cx, cy = cxr * ca - cyr * sa, cxr * sa + cyr * ca
    L, W = xb - xa, yb - ya
    if W > L:
        L, W, ang = W, L, ang + math.pi / 2
    return cx, cy, ang % math.pi, L, W


def analyze_fence(ring_xy) -> dict:
    """围栏分析：面积 / 最小旋转外接矩形 / 主方向 / 长宽比。"""
    hull = convex_hull(ring_xy)
    area = abs(sum(ring_xy[i][0] * ring_xy[(i + 1) % len(ring_xy)][1]
                   - ring_xy[(i + 1) % len(ring_xy)][0] * ring_xy[i][1]
                   for i in range(len(ring_xy)))) / 2
    cx, cy, ang, L, W = min_area_rect(hull)
    return {"area": area, "angle": ang, "aspect": L / max(W, 1e-9),
            "rect": (cx, cy, ang, L, W), "hull": hull}


def stadium_fence_index(polys, bd_pts):
    """打卡点落在哪个围栏里（按命中数最多者）。返回索引或 None。"""
    best, best_n = None, 0
    for i, ring in enumerate(polys):
        n = sum(1 for la, lo in bd_pts if in_ring(ring, la, lo))
        if n > best_n:
            best, best_n = i, n
    return best


def clamp_to_ring(locs, ring) -> None:
    """出界点向围栏质心二分回拉，直到进入场内（保留大致形状）。"""
    if not ring:
        return
    clat = sum(p[0] for p in ring) / len(ring)
    clng = sum(p[1] for p in ring) / len(ring)
    for q in locs:
        for _ in range(12):
            if in_ring(ring, q["gLat"], q["gLng"]):
                break
            q["gLat"] = clat + (q["gLat"] - clat) * 0.5
            q["gLng"] = clng + (q["gLng"] - clng) * 0.5


def ring_point_at(dense, arcs, s):
    """闭合折线环上取弧长 s 处的点（二分+线性插值）。"""
    total = arcs[-1] or 1.0
    s %= total
    i = max(0, bisect.bisect_left(arcs, s) - 1)
    i = min(i, len(dense) - 1)
    a, b = dense[i], dense[(i + 1) % len(dense)]
    seg = arcs[i + 1] - arcs[i] if i + 1 < len(arcs) else total - arcs[i]
    if seg <= 0:
        return a
    t = (s - arcs[i]) / seg
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def ring_tangent(dense, arcs, s):
    """环上弧长 s 处的单位切向量（前后差分）。"""
    a = ring_point_at(dense, arcs, s - 0.8)
    b = ring_point_at(dense, arcs, s + 0.8)
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def make_point_ring(bd_pts):
    """打卡点 -> 质心角序 -> 闭合 Catmull-Rom(18/段) -> (dense, arcs, 中心)。

    Rust generator.rs 的经典环拟合路径（已移植保留，体育场拟合为默认）。
    """
    n = len(bd_pts)
    cx = sum(p[0] for p in bd_pts) / n
    cy = sum(p[1] for p in bd_pts) / n
    ordered = sorted(bd_pts, key=lambda p: math.atan2(p[0] - cx, p[1] - cy))
    plane = [((q[1] - cy) * MET_LNG, (q[0] - cx) * MET_LAT) for q in ordered]
    dense = []
    for i in range(n):
        p0, p1, p2, p3 = plane[(i - 1) % n], plane[i], plane[(i + 1) % n], plane[(i + 2) % n]
        for j in range(18):
            t = j / 18
            t2, t3 = t * t, t * t * t
            dense.append((
                0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
                0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3),
            ))
    arcs = [0.0]
    for i in range(1, len(dense) + 1):
        a, b = dense[i - 1], dense[i % len(dense)]
        arcs.append(arcs[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return dense, arcs, (cx, cy)
