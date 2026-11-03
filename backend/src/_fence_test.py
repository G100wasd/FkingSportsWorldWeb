"""临时测试：登录 -> 拉围栏多边形+runAreaId -> 拉打卡点 -> matplotlib 可视化。

直接运行：python _fence_test.py
打卡点接口有服务端限流（5分钟3次），重复跑前稍微等一会。
"""

import datetime as dt
import json
import math
import random
import time
from pathlib import Path

import matplotlib
import numpy as np

import matplotlib.pyplot as plt
plt.show()  # 无窗口环境也能出图；想弹窗改成 plt.show()

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

from sportsworld.services import geo
from sportsworld.services.auth import login

USERNAME = "18928206383"
PASSWORD = "20061030Cjj"

# 锚点（BD-09）：校区西侧坐标，服务端就近分配跑步区
ANCHOR_LAT = 22.9058142
ANCHOR_LON = 113.8709786
# 请求时间戳伪装（当天 12:00）
FAKE_MS = int(dt.datetime.combine(dt.date.today(), dt.time(12, 0)).timestamp() * 1000)

OUT_PNG = "fence_points.png"
TRACK_PNG = "track_fit.png"
POINTS_CACHE = Path(__file__).parent / "_points_cache.json"
CACHE_TTL_S = 290

# 轨迹目标：2.25km ±0.25km
TRACK_DIST = 2250.0
TRACK_DUR = 720          # 秒，均速 3.125 m/s ≈ 5'20"/km
TRACK_SEED = 7


def main() -> None:
    sess = login(USERNAME, PASSWORD)
    try:
        fence = geo.fetch_geo_fence(sess)
        polys = fence["polygons"]
        run_area_id = fence["run_area_id"]
        print(f"[fence] runAreaId={run_area_id} polygons={len(polys)} "
              f"points={sum(len(r) for r in polys)}")
        for i, raw in enumerate(fence["raw"]):
            meta = {k: raw.get(k) for k in
                    ("campusId", "campusName", "fenceName", "name", "runAreaId", "fenceId", "type")
                    if raw.get(k) is not None}
            print(f"  fence{i}: {meta or '(无元数据)'} 顶点={len(raw.get('points', []))}")
        if not polys:
            print("[fence] 无围栏多边形，无法确定锚点，结束")
            return

        # 指定锚点 + 假时间戳拉点位（带 300s 本地缓存防 10603 限流）
        a_lat, a_lon = ANCHOR_LAT, ANCHOR_LON
        print(f"[points] 锚点 BD=({a_lat:.6f},{a_lon:.6f}) fake_ts=12:00")
        pts = fetch_points_cached(sess, a_lat, a_lon, run_area_id)
        print(f"[points] {len(pts)} 个打卡点")
        for p in pts[:10]:
            xy = geo.point_xy(p)
            print(f"  {p.get('pointName','')} BD={xy} must={p.get('isMustPoint')}")

        # --- 可视化 ---
        fig, ax = plt.subplots(figsize=(9, 9))
        for i, ring in enumerate(polys):
            xs = [p[1] for p in ring] + [ring[0][1]]
            ys = [p[0] for p in ring] + [ring[0][0]]
            ax.fill(xs, ys, alpha=0.15, label=f"fence{i}" if len(polys) > 1 else "geoFence")
            ax.plot(xs, ys, linewidth=1.2)
        ax.plot(a_lon, a_lat, "k^", markersize=10, label="anchor")

        bd = geo.points_bd(pts)
        xs = [p[1] for p in bd]
        ys = [p[0] for p in bd]
        ax.scatter(xs, ys, c="red", s=60, zorder=5, label="打卡点")
        for i, (p, (la, lo)) in enumerate(zip(pts, bd)):
            name = p.get("pointName") or f"P{i}"
            must = "*" if p.get("isMustPoint") else ""
            ax.annotate(f"{name}{must}", (lo, la), fontsize=8,
                        xytext=(5, 5), textcoords="offset points")

        ax.set_xlabel("longitude (BD-09)")
        ax.set_ylabel("latitude (BD-09)")
        ax.set_title(f"geoFence + points  (runAreaId={run_area_id}, {len(pts)} pts)")
        ax.legend()
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(alpha=0.3)
        fig.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
        print(f"[plot] 已保存 {OUT_PNG}")

        # --- 轨迹拟合（体育场跑道：两直道 + 两半圆） ---
        bd = geo.points_bd(pts)
        stadium_idx = stadium_fence_index(polys, bd)
        if stadium_idx is None:
            print("[stadium] 打卡点不在任何围栏内，无法确定跑步区域，跳过拟合")
            return
        stadium_ring = polys[stadium_idx]
        # 参考中心 = 体育场围栏质心（仅初始化，不固定拟合中心）
        sc_lat = sum(p[0] for p in stadium_ring) / len(stadium_ring)
        sc_lng = sum(p[1] for p in stadium_ring) / len(stadium_ring)
        sd_dense, sd_arcs, sd_c, fit = make_stadium_ring(bd, (sc_lat, sc_lng), stadium_ring)
        print(f"[stadium] 中心=({fit['cx_bd'][0]:.6f},{fit['cx_bd'][1]:.6f}) "
              f"angle={math.degrees(fit['angle']):.1f}° L={fit['straight']:.1f}m "
              f"r={fit['radius']:.1f}m 周长={fit['perimeter']:.0f}m "
              f"边界余量={fit['fence_margin']:.1f}m")
        print("          拟合误差(m): " + " ".join(f"{e:.2f}" for e in fit["point_errors"]))
        locs = build_track(TRACK_DIST, TRACK_DUR, TRACK_SEED, bd,
                           fence_ring=stadium_ring,
                           ring=(sd_dense, sd_arcs, sd_c))
        # 统计
        inside = sum(1 for q in locs
                     if stadium_idx is not None and in_ring(polys[stadium_idx], q["gLat"], q["gLng"]))
        snapped = sum(1 for q in locs if q.get("snapped"))
        print(f"[track] {len(locs)} 点 目标 {TRACK_DIST}m / {TRACK_DUR}s "
              f"累计={locs[-1]['totalDis']:.1f}m "
              f"场内点={inside}/{len(locs)} 吸附={snapped}")

        fig2, ax2 = plt.subplots(figsize=(9, 9))
        for i, ring in enumerate(polys):
            xs = [p[1] for p in ring] + [ring[0][1]]
            ys = [p[0] for p in ring] + [ring[0][0]]
            ax2.fill(xs, ys, alpha=0.12)
            ax2.plot(xs, ys, linewidth=1.2,
                     label=fence["raw"][i].get("name") if i < len(fence["raw"]) else f"fence{i}")
        ax2.scatter([p[1] for p in bd], [p[0] for p in bd],
                    c="red", s=70, zorder=6, label="打卡点")
        # 拟合跑道轮廓（虚线，BD 系反算）
        sd_lat = [sd_c[0] + y / MET_LAT for _, y in sd_dense]
        sd_lng = [sd_c[1] + x / MET_LNG for x, _ in sd_dense]
        ax2.plot(sd_lng, sd_lat, "--", color="gray", linewidth=1.0,
                 alpha=0.8, zorder=3, label="拟合跑道")
        ax2.plot([q["gLng"] for q in locs], [q["gLat"] for q in locs],
                 "-", color="tab:blue", linewidth=1.0, alpha=0.8, zorder=4, label="拟合轨迹")
        ax2.plot(locs[0]["gLng"], locs[0]["gLat"], "go", markersize=9, zorder=7, label="起点")
        ax2.plot(locs[-1]["gLng"], locs[-1]["gLat"], "ks", markersize=9, zorder=7, label="终点")
        ax2.plot(a_lon, a_lat, "k^", markersize=8, alpha=0.4, label="anchor")
        ax2.set_xlabel("longitude (BD-09)")
        ax2.set_ylabel("latitude (BD-09)")
        ax2.set_title(f"track fit  {TRACK_DIST:.0f}m / {TRACK_DUR}s, {len(locs)} pts")
        ax2.legend()
        ax2.set_aspect("equal", adjustable="datalim")
        ax2.grid(alpha=0.3)
        fig2.savefig(TRACK_PNG, dpi=150, bbox_inches="tight")
        print(f"[plot] 已保存 {TRACK_PNG}")
    finally:
        sess.logout()


# ---------- 点位本地缓存（300s，对齐服务端限流） ----------

def fetch_points_cached(sess, lat: float, lon: float, run_area_id) -> list[dict]:
    key = f"{sess.uid}|{lat:.4f}|{lon:.4f}|{run_area_id}"
    if POINTS_CACHE.exists():
        try:
            cache = json.loads(POINTS_CACHE.read_text(encoding="utf-8"))
            ent = cache.get(key)
            if ent and time.time() - ent["ts"] < CACHE_TTL_S:
                print(f"[points] 本地缓存命中（{int(time.time() - ent['ts'])}s 前）")
                return ent["points"]
        except (json.JSONDecodeError, KeyError):
            pass
    pts = geo.fetch_points(sess, lat, lon, run_area_id, ts_ms=FAKE_MS)
    cache = {}
    if POINTS_CACHE.exists():
        try:
            cache = json.loads(POINTS_CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache = {}
    cache[key] = {"ts": time.time(), "points": pts}
    POINTS_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return pts


# ---------- Legacy 轨迹生成器（精简移植 src/track/generator.rs） ----------

MET_LAT = 111_132.0
MET_LNG = 86_600.0
SPEED_FLOOR = 1.90
SPEED_CEIL = 6.30
MAX_DRIFT = 6.0


def _weighted(rng: random.Random, items):
    total = sum(w for _, w in items)
    u = rng.uniform(0, total)
    for v, w in items:
        u -= w
        if u < 0:
            return v
    return items[-1][0]


def make_point_ring(bd_pts):
    """打卡点 -> 质心角序 -> 闭合 Catmull-Rom(18/段) -> (dense, arcs, 中心)。"""
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


STADIUM_BUFFER = 2.0   # 跑道与围栏边界的安全距离（m）


def _seg_dist(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    vv = vx * vx + vy * vy
    t = 0.0 if vv <= 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / vv))
    return math.hypot(px - ax - t * vx, py - ay - t * vy)


def _poly_signed_dist(ring_xy, px, py):
    """点到多边形边界的带符号距离（m）：>0 场内，<=0 场外。"""
    inside = False
    j = len(ring_xy) - 1
    dmin = 1e18
    for i in range(len(ring_xy)):
        xi, yi = ring_xy[i]
        xj, yj = ring_xy[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        d = _seg_dist(px, py, xi, yi, xj, yj)
        if d < dmin:
            dmin = d
        j = i
    return dmin if inside else -dmin


def _stadium_outline(cx, cy, ang, L, r, n):
    """按弧长均匀采样闭合跑道中心线（局部米制）。

    上直道 -> 右半圆 -> 下直道 -> 左半圆。参数化保证几何连续、
    简单闭合、不可能自交。
    """
    per = 2.0 * L + 2.0 * math.pi * r
    ca, sa = math.cos(ang), math.sin(ang)
    out = []
    for i in range(n):
        t = per * i / n
        if t < L:
            u, v = -L / 2 + t, r
        elif t < L + math.pi * r:
            a = (t - L) / r
            u, v = L / 2 + r * math.sin(a), r * math.cos(a)
        elif t < 2 * L + math.pi * r:
            u, v = L / 2 - (t - L - math.pi * r), -r
        else:
            a = (t - 2 * L - math.pi * r) / r
            u, v = -L / 2 - r * math.sin(a), -r * math.cos(a)
        out.append((cx + u * ca - v * sa, cy + u * sa + v * ca))
    return out


def make_stadium_ring(bd_pts, center_ll, fence_ring_ll, n=720, buffer=STADIUM_BUFFER):
    """体育场跑道拟合：min Σ dist(点, 跑道中心线)²，硬约束整条跑道在围栏内。

    参数 (cx, cy, angle, straightLength, radius)；等权重软约束，不强制过点。
    参考中心只作为局部坐标系原点和初值，不固定拟合中心。
    返回 (dense, arcs, center_ll, result)；无可行解时抛 RuntimeError。
    """
    from scipy.optimize import minimize

    clat, clng = center_ll
    plane = np.array([[(lo - clng) * MET_LNG, (la - clat) * MET_LAT] for la, lo in bd_pts])
    fence_xy = [((lo - clng) * MET_LNG, (la - clat) * MET_LAT) for la, lo in fence_ring_ll]

    # 初始方向：打卡点 PCA 主轴
    mean = plane.mean(axis=0)
    cov = np.cov((plane - mean).T)
    vals, vecs = np.linalg.eigh(cov)
    theta0 = math.atan2(vecs[1, -1], vecs[0, -1]) % math.pi

    def obj(p):
        cx, cy, ang, L, r = p
        ca, sa = math.cos(ang), math.sin(ang)
        q = plane - np.array([cx, cy])
        s = q[:, 0] * ca + q[:, 1] * sa
        d = -q[:, 0] * sa + q[:, 1] * ca
        dist = np.abs(np.hypot(np.maximum(np.abs(s) - L / 2, 0.0), d) - r)
        return float(np.sum(dist * dist))

    def cons(p, m=96):
        return np.array([_poly_signed_dist(fence_xy, x, y) - buffer
                         for x, y in _stadium_outline(*p, m)])

    bounds = [(-300.0, 300.0), (-300.0, 300.0), (0.0, math.pi),
              (10.0, 400.0), (4.0, 200.0)]

    best = None
    worst_margin = -1e18
    for k in range(-9, 10):
        ang0 = (theta0 + math.radians(10 * k)) % math.pi
        ca, sa = math.cos(ang0), math.sin(ang0)
        q = plane - mean
        s = q[:, 0] * ca + q[:, 1] * sa
        d = -q[:, 0] * sa + q[:, 1] * ca
        L0 = min(400.0, max(20.0, float(s.max() - s.min())))
        r0 = min(200.0, max(6.0, float(d.max() - d.min()) / 2))
        for Lg, rg in ((L0, r0), (min(400.0, L0 * 1.15), max(4.0, r0 * 0.8))):
            res = minimize(obj, [float(mean[0]), float(mean[1]), ang0, Lg, rg],
                           method="SLSQP", bounds=bounds,
                           constraints=[{"type": "ineq", "fun": cons}],
                           options={"maxiter": 200, "ftol": 1e-9})
            p = res.x
            margin = min(_poly_signed_dist(fence_xy, x, y)
                         for x, y in _stadium_outline(*p, n))
            if margin > worst_margin:
                worst_margin = margin
            if margin >= buffer - 1e-6 and p[3] > 9.9 and p[4] > 3.9:
                if best is None or res.fun < best[0]:
                    best = (res.fun, p, margin)

    if best is None:
        raise RuntimeError(
            f"体育场跑道无可行解：所有候选均越界"
            f"（最大边界余量 {worst_margin:.2f}m < 要求 {buffer}m），已放弃输出")

    cx, cy, ang, L, r = best[1]
    dense = _stadium_outline(cx, cy, ang, L, r, n)
    arcs = [0.0]
    for i in range(1, n + 1):
        a, b = dense[i - 1], dense[i % n]
        arcs.append(arcs[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))

    ca, sa = math.cos(ang), math.sin(ang)
    errs = []
    for px, py in plane:
        s = (px - cx) * ca + (py - cy) * sa
        d = -(px - cx) * sa + (py - cy) * ca
        errs.append(abs(math.hypot(max(abs(s) - L / 2, 0.0), d) - r))

    result = {
        "cx_bd": (clat + cy / MET_LAT, clng + cx / MET_LNG),
        "cx_m": cx, "cy_m": cy,
        "angle": ang,
        "straight": L, "radius": r,
        "perimeter": arcs[-1],
        "point_errors": errs,
        "fence_margin": best[2],
        "buffer": buffer,
        "track_bd": [(clat + y / MET_LAT, clng + x / MET_LNG) for x, y in dense],
    }
    return dense, arcs, (clat, clng), result


def ring_point_at(dense, arcs, s):
    total = arcs[-1] or 1.0
    s %= total
    import bisect
    i = max(0, bisect.bisect_left(arcs, s) - 1)
    i = min(i, len(dense) - 1)
    a, b = dense[i], dense[(i + 1) % len(dense)]
    seg = arcs[i + 1] - arcs[i] if i + 1 < len(arcs) else total - arcs[i]
    if seg <= 0:
        return a
    t = (s - arcs[i]) / seg
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def in_ring(ring, lat, lon) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if (xi > lon) != (xj > lon) and lat < (yj - yi) * (lon - xi) / (xj - xi) + yi:
            inside = not inside
        j = i
    return inside


def stadium_fence_index(polys, bd_pts):
    """打卡点落在哪个围栏里（按命中数最多者）。"""
    best, best_n = None, 0
    for i, ring in enumerate(polys):
        n = sum(1 for la, lo in bd_pts if in_ring(ring, la, lo))
        if n > best_n:
            best, best_n = i, n
    return best


def fit_speeds(w, dts, target):
    for _ in range(24):
        cur = sum(x * dt for x, dt in zip(w, dts))
        if abs(cur - target) <= 1.0:
            break
        k = target / cur
        w = [min(max(x * k, SPEED_FLOOR), SPEED_CEIL) for x in w]
    return [min(max(x, SPEED_FLOOR), SPEED_CEIL) for x in w]


def clamp_to_ring(locs, ring):
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


def build_track(dist, dur, seed, points_bd, fence_ring=None, ring=None):
    """返回 [{gLat,gLng,totalTime,totalDis,ptype,speed,...}]，几何/速度与 Rust Legacy 一致。

    ring=(dense,arcs,center) 可注入自定义环（如椭圆）；None 时用 Catmull-Rom。"""
    rng = random.Random(seed)
    dur_f = float(dur)
    if ring is None:
        dense, arcs, (c_lat, c_lng) = make_point_ring(points_bd)
    else:
        dense, arcs, (c_lat, c_lng) = ring
    direction = rng.choice([1.0, -1.0])
    s = rng.uniform(0.0, arcs[-1] or 400.0)
    phase_v = rng.uniform(0.0, math.tau)
    phase_l = rng.uniform(0.0, math.tau)

    times = []
    t = 0.0
    while t < dur_f:
        times.append(t)
        t += 5.0 if rng.random() < 0.80 else rng.choice([1.0, 2.0, 3.0, 4.0, 6.0, 7.0, 8.0])
    n = len(times)

    dips = [(rng.uniform(0.15, 0.75) * dur_f, rng.uniform(25.0, 55.0), rng.uniform(0.08, 0.16))
            for _ in range(rng.choice([1, 1, 2]))]

    def dip_factor(tt):
        f = 1.0
        for c, hw, d in dips:
            if abs(tt - c) < hw:
                f *= 1.0 - d * 0.5 * (1.0 + math.cos(math.pi * (tt - c) / hw))
        return f

    base = dist / dur_f
    w = []
    for tt in times:
        ramp = (0.85 + 0.15 * (tt / max(1.0, min(8.0, dur_f / 20.0)))) if tt < 8.0 else 1.0
        ramp = min(ramp, 1.0) * (1.0 + 0.03 * (tt - (dur_f - 8.0)) / 8.0 if tt > dur_f - 8.0 else 1.0)
        km_done = (tt / dur_f) * dist / 1000.0
        fatigue = 1.03 if km_done <= 0.5 else max(0.80, 1.03 - 0.06 * (km_done - 0.5))
        wave = (1.0 + 0.010 * math.sin(math.tau * tt / 115.0 + phase_v)
                + 0.035 * math.sin(math.tau * tt / 47.0 + phase_v * 2.3)
                + 0.015 * math.sin(math.tau * tt / 19.0 + phase_v * 3.7)) * dip_factor(tt)
        w.append(base * ramp * fatigue * wave * (1.0 + rng.gauss(0.0, 0.008)))

    dts = [times[i + 1] - times[i] for i in range(n - 1)] + [max(1.0, dur_f - times[-1])]
    w = fit_speeds(w, dts, dist)
    seg = [w[i] * dts[i] for i in range(n)]

    tl_w = [((-1, 4), 54), ((-1, 1), 27), ((-1, 12), 13), ((-1, 5), 3), ((-1, 6), 2)]
    kinds = []
    for _ in range(n):
        u = rng.random()
        if u < 0.39:
            kinds.append((3, 1))
        elif u < 0.93:
            kinds.append((0, 1))
        elif u < 0.96:
            kinds.append(_weighted(rng, tl_w))
        else:
            kinds.append((rng.choice([1, 1, 1, 1, 2, 2]), 1))
    for i in range(1, n):
        prev_drift = kinds[i - 1][0] in (-1, 5, 6)
        if kinds[i][0] != -1 and prev_drift and rng.random() < 0.08:
            kinds[i] = (7 if rng.random() < 0.75 else 8, 1)
    for i in range(1, n):
        if kinds[i][0] == -1 and kinds[i - 1][0] == -1:
            kinds[i] = (rng.choice([3, 0]), 1)

    normal = [i for i in range(n) if kinds[i][0] != -1 and i > 1]
    share = sum(seg[i] for i in normal) or 1.0
    disp = [0.0] * n
    for i in normal:
        disp[i] = seg[i] * (dist / share)

    locs = []
    t_acc = dist_acc = 0.0
    jx = jy = 0.0
    for i in range(n):
        dt_i, (typ, _lt) = dts[i], kinds[i]
        t_acc += dt_i
        if typ != -1:
            s += direction * disp[i]
            x, y = ring_point_at(dense, arcs, s)
            jx = 0.72 * jx + rng.gauss(0.0, 0.75)
            jy = 0.72 * jy + rng.gauss(0.0, 0.75)
            px, py = x + jx, y + jy
            rad = round(rng.uniform(1.4, 5.1) if typ == 3 else rng.uniform(1.4, 2.4), 2)
            dist_acc += disp[i]
        else:
            d_step = rng.uniform(0.0, MAX_DRIFT) if rng.random() >= 0.20 else 0.0
            x, y = ring_point_at(dense, arcs, s)
            if d_step > 0:
                ang = rng.uniform(0.0, math.tau)
                px, py = x + d_step * math.sin(ang), y + d_step * math.cos(ang)
            elif locs:
                px = (locs[-1]["gLng"] - c_lng) * MET_LNG
                py = (locs[-1]["gLat"] - c_lat) * MET_LAT
            else:
                px, py = x + jx, y + jy
            rad = round(rng.uniform(1.6, 10.0), 2)
        lat = c_lat + py / MET_LAT
        lng = c_lng + px / MET_LNG
        avg_sp = round(dist_acc / max(1.0, t_acc), 4) if typ == -1 else round(disp[i] / dt_i, 4)
        locs.append({
            "id": i + 1, "gLat": round(lat, 7), "gLng": round(lng, 7),
            "speed": round(avg_sp * 3.6, 4), "avgSpeed": avg_sp,
            "radius": rad, "ptype": typ,
            "totalTime": round(t_acc), "totalDis": round(dist_acc, 4),
        })
    # 点位吸附：40m 内最近正常点精确落位
    snap_targets = points_bd
    for plat, plng in snap_targets:
        best_i, best_d = None, 1e18
        for i, q in enumerate(locs):
            if q["ptype"] == -1:
                continue
            dd = ((q["gLat"] - plat) * MET_LAT) ** 2 + ((q["gLng"] - plng) * MET_LNG) ** 2
            if dd < best_d:
                best_i, best_d = i, dd
        if best_i is not None and best_d < 40.0 ** 2:
            locs[best_i]["gLat"], locs[best_i]["gLng"] = plat, plng
            locs[best_i]["snapped"] = True
    clamp_to_ring(locs, fence_ring)
    return locs


if __name__ == "__main__":
    main()
