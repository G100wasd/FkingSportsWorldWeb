"""临时测试：登录 -> 拉围栏多边形+runAreaId -> 拉打卡点 -> matplotlib 可视化。

直接运行：python _fence_test.py
打卡点接口有服务端限流（5分钟3次），重复跑前稍微等一会。
"""

import datetime as dt
import json
import math
import random

import matplotlib
import numpy as np

import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

from sportsworld.services import geo, policy
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

# 轨迹目标：2.25km ±0.25km
TRACK_DIST = 2250.0
TRACK_DUR = 720          # 秒，均速 3.125 m/s ≈ 5'20"/km
TRACK_SEED = None        # None=每次随机；填整数可复现某次轨迹


def main() -> None:
    sess = login(USERNAME, PASSWORD)
    try:
        fence = geo.fetch_geo_fence(sess)
        polys = fence["polygons"]
        run_area_id = fence["run_area_id"]
        print(f"[fence] runAreaId={run_area_id} polygons={len(polys)} "
              f"points={sum(len(r) for r in polys)}")
        for i, raw in enumerate(fence["raw"]):
            meta = {k: v for k, v in raw.items() if k != "points"}
            print(f"  fence{i}: {json.dumps(meta, ensure_ascii=False) or '(无元数据)'} "
                  f"顶点={len(raw.get('points', []))}")
        if not polys:
            print("[fence] 无围栏多边形，无法确定锚点，结束")
            return

        # --- 开启计分跑：runMode=1 + 假时间戳 ---
        # 计分跑打卡点可能内嵌在 policy 响应（mustPoints 等字段）而非点位接口
        scoring_pts: list[tuple[float, float]] = []
        print("[scoring] 以假时间 12:00 申请开启计分跑(runMode=1)...")
        try:
            biz = policy.fetch_policy(sess, run_mode=1, ts_ms=FAKE_MS)
            summ = policy.policy_summary(biz)
            data = biz.get("data") if isinstance(biz.get("data"), dict) else {}
            print(f"[scoring] 开启成功 minDistance={summ['minDistance']} "
                  f"validTime={summ['validTime']} policy={summ['policy']}")
            print(f"[scoring] data keys: {sorted(data.keys())}")
            scoring_pts = summ["must_points"]
            print(f"[scoring] 内嵌打卡点 {len(scoring_pts)} 个")
            for la, lo in scoring_pts:
                print(f"  must BD=({la:.6f},{lo:.6f})")
        except RuntimeError as e:
            print(f"[scoring] 开启被拒绝: {e}")

        # 指定锚点 + 假时间戳，每次运行直连服务端拉实时点位
        # 注意：接口限流约 5 分钟 3 次（10603），频繁运行会触发
        a_lat, a_lon = ANCHOR_LAT, ANCHOR_LON
        print(f"[points] 锚点 BD=({a_lat:.6f},{a_lon:.6f}) fake_ts=12:00")
        pts = geo.fetch_points(sess, a_lat, a_lon, run_area_id, ts_ms=FAKE_MS)
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
        # 拟合输入固定为服务端实时下发的 5 个打卡点
        bd = geo.points_bd(pts)
        fit_pts = bd
        stadium_idx = stadium_fence_index(polys, fit_pts)
        if stadium_idx is None:
            print("[stadium] 打卡点不在任何围栏内，无法确定跑步区域，跳过拟合")
            return
        stadium_ring = polys[stadium_idx]
        # 参考中心 = 体育场围栏质心（仅初始化，不固定拟合中心）
        sc_lat = sum(p[0] for p in stadium_ring) / len(stadium_ring)
        sc_lng = sum(p[1] for p in stadium_ring) / len(stadium_ring)
        sd_dense, sd_arcs, sd_c, fit = make_stadium_ring(fit_pts, (sc_lat, sc_lng), stadium_ring)
        fa = fit["fence"]
        print(f"[fence0] 面积={fa['area']:.0f}m² 外接矩形={fa['rect'][3]:.0f}x{fa['rect'][4]:.0f}m "
              f"主方向={math.degrees(fa['angle']):.1f}° 长宽比={fa['aspect']:.2f}")
        print(f"[stadium] 中心=({fit['cx_bd'][0]:.6f},{fit['cx_bd'][1]:.6f}) "
              f"angle={math.degrees(fit['angle']):.1f}° 直道L={fit['straight']:.1f}m "
              f"半圆r={fit['radius']:.1f}m 总尺寸={fit['total_len']:.0f}x{fit['total_wid']:.0f}m "
              f"周长={fit['perimeter']:.0f}m")
        print(f"          误差(m): {' '.join(f'{e:.2f}' for e in fit['point_errors'])} "
              f"| 均={fit['mean_err']:.2f} 最大={fit['max_err']:.2f}")
        print(f"          边界余量={fit['fence_margin']:.1f}m(≥{fit['buffer']}) "
              f"包含=PASS 收敛={fit['converged']} 相近可行解={fit['alt_solutions']}"
              f"{' [尺寸不确定]' if fit['size_uncertain'] else ''}")
        seed = (TRACK_SEED if TRACK_SEED is not None
                else random.SystemRandom().randrange(2 ** 31))
        print(f"[track] seed={seed}（填回 TRACK_SEED 可复现本次轨迹）")
        locs = build_track(TRACK_DIST, TRACK_DUR, seed, fit_pts,
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
        if scoring_pts:
            ax2.scatter([p[1] for p in scoring_pts], [p[0] for p in scoring_pts],
                        c="darkorange", marker="D", s=80, zorder=6, label="计分跑点位")
        # 拟合跑道轮廓（虚线，BD 系反算）
        sd_lat = [sd_c[0] + y / MET_LAT for _, y in sd_dense]
        sd_lng = [sd_c[1] + x / MET_LNG for x, _ in sd_dense]
        ax2.plot(sd_lng, sd_lat, "--", color="gray", linewidth=1.0,
                 alpha=0.8, zorder=3, label="拟合跑道")
        # 参考中心 / 拟合中心 / 点到跑道最近点连线
        ax2.plot(sc_lng, sc_lat, "m+", markersize=14, markeredgewidth=2,
                 zorder=7, label="参考中心")
        ax2.plot(fit["cx_bd"][1], fit["cx_bd"][0], "c*", markersize=14,
                 zorder=7, label="拟合中心")
        for (la, lo), (qx, qy) in zip(fit_pts, fit["closest"]):
            ax2.plot([lo, sd_c[1] + qx / MET_LNG], [la, sd_c[0] + qy / MET_LAT],
                     ":", color="orange", linewidth=1.0, zorder=5)
        info = (f"L={fit['straight']:.0f}m r={fit['radius']:.0f}m "
                f"周长={fit['perimeter']:.0f}m\n"
                f"err: {' '.join(f'{e:.1f}' for e in fit['point_errors'])} "
                f"(max {fit['max_err']:.1f}m)\n"
                f"margin={fit['fence_margin']:.1f}m")
        ax2.text(0.02, 0.02, info, transform=ax2.transAxes, fontsize=8,
                 va="bottom", bbox=dict(facecolor="white", alpha=0.7, edgecolor="gray"))
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
        plt.show()
    finally:
        sess.logout()


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


def _convex_hull(pts):
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


def _min_area_rect(hull):
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


def _analyze_fence(ring_xy):
    """fence0 分析：面积 / 最小旋转外接矩形 / 主方向 / 长宽比。"""
    hull = _convex_hull(ring_xy)
    area = abs(sum(ring_xy[i][0] * ring_xy[(i + 1) % len(ring_xy)][1]
                   - ring_xy[(i + 1) % len(ring_xy)][0] * ring_xy[i][1]
                   for i in range(len(ring_xy)))) / 2
    cx, cy, ang, L, W = _min_area_rect(hull)
    return {"area": area, "angle": ang, "aspect": L / max(W, 1e-9),
            "rect": (cx, cy, ang, L, W), "hull": hull}


def _track_closest(cx, cy, ang, L, r, px, py):
    """点到跑道中心线的最近点（局部坐标），用于可视化误差连线。"""
    ca, sa = math.cos(ang), math.sin(ang)
    s = (px - cx) * ca + (py - cy) * sa
    d = -(px - cx) * sa + (py - cy) * ca
    if abs(s) <= L / 2:
        u, v = s, math.copysign(r, d)
    else:
        ex = math.copysign(L / 2, s)
        dx, dy = s - ex, d
        nrm = math.hypot(dx, dy) or 1e-9
        u, v = ex + dx / nrm * r, dy / nrm * r
    return cx + u * ca - v * sa, cy + u * sa + v * ca


# 多目标权重：词典序优先围栏包含(硬约束)，其次几何合理，再贴合点位
LAM_PTS = 1.0
LAM_SHAPE = 60.0
LAM_CENTER = 0.02
LAM_PRIOR = 0.05
HUBER_D = 5.0                          # 鲁棒核阈值（m），压低异常点影响
ASPECT_LO, ASPECT_HI = 1.55, 4.0       # (L+2r)/(2r) 软约束合理区间
ASPECT_HARD_LO, ASPECT_HARD_HI = 1.35, 5.5  # 硬约束区间
STD_PERIMETERS = (200.0, 250.0, 300.0, 350.0, 400.0)  # 常见跑道周长先验


def make_stadium_ring(bd_pts, center_ll, fence_ring_ll, n=720, buffer=STADIUM_BUFFER):
    """标准田径场拟合 V2：还原真实跑道优先于贴合点位。

    词典序：围栏包含(硬约束) > 几何合理 > 贴合点位 > 靠近参考中心。
    目标 E = λ1·E_points(Huber) + λ2·E_shape(长宽比) + λ3·E_center + λ4·E_prior。
    参数 (cx, cy, angle, straightLength, radius)；参考中心仅初始化，不固定。
    返回 (dense, arcs, center_ll, result)；无可行解时抛 RuntimeError。
    """
    from scipy.optimize import minimize

    clat, clng = center_ll
    plane = np.array([[(lo - clng) * MET_LNG, (la - clat) * MET_LAT] for la, lo in bd_pts])
    fence_xy = [((lo - clng) * MET_LNG, (la - clat) * MET_LAT) for la, lo in fence_ring_ll]
    fx = _analyze_fence(fence_xy)
    rcx, rcy, rang, rL, rW = fx["rect"]
    pts_mean = plane.mean(axis=0)

    def resid(p):
        cx, cy, ang, L, r = p
        ca, sa = math.cos(ang), math.sin(ang)
        q = plane - np.array([cx, cy])
        s = q[:, 0] * ca + q[:, 1] * sa
        d = -q[:, 0] * sa + q[:, 1] * ca
        return np.abs(np.hypot(np.maximum(np.abs(s) - L / 2, 0.0), d) - r)

    def obj(p):
        cx, cy, ang, L, r = p
        d = resid(p)
        e_pts = float(np.sum(np.where(d < HUBER_D, d * d / 2,
                                      HUBER_D * (d - HUBER_D / 2))))
        aspect = (L + 2 * r) / (2 * r)
        v = max(0.0, ASPECT_LO - aspect) + max(0.0, aspect - ASPECT_HI)
        t_len, t_wid = L + 2 * r, 2 * r
        e_prior = (max(0.0, 0.60 * rL - t_len) + max(0.0, t_len - 0.95 * rL)) ** 2 \
                + (max(0.0, 0.55 * rW - t_wid) + max(0.0, t_wid - 0.90 * rW)) ** 2
        per = 2 * L + 2 * math.pi * r
        std = min(STD_PERIMETERS, key=lambda s_: abs(per - s_))
        dev = max(0.0, abs(per - std) - 0.18 * std)
        e_prior += 0.3 * dev * dev
        return (LAM_PTS * e_pts + LAM_SHAPE * v * v
                + LAM_CENTER * (cx * cx + cy * cy) + LAM_PRIOR * e_prior)

    def cons(p, m=72):
        L, r = p[3], p[4]
        aspect = (L + 2 * r) / (2 * r)
        hard = [aspect - ASPECT_HARD_LO, ASPECT_HARD_HI - aspect, L - 5.0, r - 2.0]
        geo = [_poly_signed_dist(fence_xy, x, y) - buffer
               for x, y in _stadium_outline(*p, m)]
        return np.array(hard + geo)

    bounds = [(-300.0, 300.0), (-300.0, 300.0), (0.0, math.pi),
              (10.0, 400.0), (4.0, 200.0)]

    # 多初始值：fence 主方向 ±15° + 正交方向；参考中心/点质心/矩形心；两组尺寸先验
    seeds_ang = sorted({(rang + math.radians(k)) % math.pi
                        for k in (-15, -5, 0, 5, 15, 90)})
    seeds_c = [(0.0, 0.0), (float(pts_mean[0]), float(pts_mean[1])), (rcx, rcy)]
    seeds_lr = []
    for rf in (0.32, 0.40):
        r0 = min(200.0, max(6.0, rW * rf))
        seeds_lr.append((min(400.0, max(15.0, rL * 0.72 - 2 * r0)), r0))

    feas, worst_margin = [], -1e18
    for a0 in seeds_ang:
        for cx0, cy0 in seeds_c:
            for L0, r0 in seeds_lr:
                res = minimize(obj, [cx0, cy0, a0, L0, r0], method="SLSQP",
                               bounds=bounds,
                               constraints=[{"type": "ineq", "fun": cons}],
                               options={"maxiter": 200, "ftol": 1e-10})
                p = res.x
                margin = min(_poly_signed_dist(fence_xy, x, y)
                             for x, y in _stadium_outline(*p, n))
                worst_margin = max(worst_margin, margin)
                if margin >= buffer - 0.1 and p[3] > 9.9 and p[4] > 3.9:
                    feas.append((res.fun, p, margin, bool(res.success)))

    if not feas:
        raise RuntimeError(
            f"体育场跑道无可行解：{len(seeds_ang) * len(seeds_c) * len(seeds_lr)} "
            f"组初值均越界（最大边界余量 {worst_margin:.2f}m < 要求 {buffer}m），"
            f"已放弃输出")

    feas.sort(key=lambda t: t[0])
    f_best, p, margin, conv = feas[0]
    cx, cy, ang, L, r = p
    # 相近可行解 & 尺寸不确定性：目标值接近但尺寸差异显著 -> 报告不确定
    near = [t for t in feas if t[0] <= f_best * 1.2 + 1e-9]
    sig = {(round(t[1][2] * 20), round(t[1][3] / 2), round(t[1][4] / 2)) for t in near}
    alt_solutions = max(0, len(sig) - 1)
    Ls = [t[1][3] for t in near]
    rs = [t[1][4] for t in near]
    size_uncertain = (max(Ls) - min(Ls) > 0.15 * L) or (max(rs) - min(rs) > 0.15 * r)

    dense = _stadium_outline(cx, cy, ang, L, r, n)
    arcs = [0.0]
    for i in range(1, n + 1):
        a, b = dense[i - 1], dense[i % n]
        arcs.append(arcs[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    # 输出采样间隔 <=1m：不够则加密重建
    if arcs[-1] > n:
        n = int(arcs[-1]) + 1
        dense = _stadium_outline(cx, cy, ang, L, r, n)
        arcs = [0.0]
        for i in range(1, n + 1):
            a, b = dense[i - 1], dense[i % n]
            arcs.append(arcs[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))

    errs = resid(p).tolist()
    track_bd = [(clat + y / MET_LAT, clng + x / MET_LNG) for x, y in dense]
    track_bd.append(track_bd[0])  # 闭合 LineString
    result = {
        "cx_bd": (clat + cy / MET_LAT, clng + cx / MET_LNG),
        "cx_m": cx, "cy_m": cy,
        "angle": ang, "straight": L, "radius": r,
        "total_len": L + 2 * r, "total_wid": 2 * r, "perimeter": arcs[-1],
        "point_errors": errs,
        "mean_err": float(np.mean(errs)), "max_err": float(np.max(errs)),
        "fence_margin": margin, "buffer": buffer,
        "contained": True, "converged": conv,
        "alt_solutions": alt_solutions, "size_uncertain": size_uncertain,
        "fence": fx, "sample_step": arcs[-1] / n,
        "track_bd": track_bd,
        "closest": [_track_closest(cx, cy, ang, L, r, px, py) for px, py in plane],
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


def ring_tangent(dense, arcs, s):
    """环上弧长 s 处的单位切向量（前向差分）。"""
    a = ring_point_at(dense, arcs, s - 0.8)
    b = ring_point_at(dense, arcs, s + 0.8)
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


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

    # 侧向摆动：两条低频正弦叠加，模拟跑者在道内的平滑走位
    # （与逐点 AR(1) 高频 GPS 抖动叠加，合成真实轨迹观感）
    wv_a1, wv_t1 = rng.uniform(0.8, 1.5), rng.uniform(70.0, 130.0)
    wv_a2, wv_t2 = rng.uniform(0.25, 0.6), rng.uniform(16.0, 30.0)
    wv_p1, wv_p2 = rng.uniform(0.0, math.tau), rng.uniform(0.0, math.tau)

    def lateral(tt):
        return (wv_a1 * math.sin(math.tau * tt / wv_t1 + wv_p1)
                + wv_a2 * math.sin(math.tau * tt / wv_t2 + wv_p2))

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
            tx, ty = ring_tangent(dense, arcs, s)
            lo = lateral(t_acc)
            jx = 0.72 * jx + rng.gauss(0.0, 0.75)
            jy = 0.72 * jy + rng.gauss(0.0, 0.75)
            px, py = x - ty * lo + jx, y + tx * lo + jy
            rad = round(rng.uniform(1.4, 5.1) if typ == 3 else rng.uniform(1.4, 2.4), 2)
            dist_acc += disp[i]
        else:
            d_step = rng.uniform(0.0, MAX_DRIFT) if rng.random() >= 0.20 else 0.0
            x, y = ring_point_at(dense, arcs, s)
            tx, ty = ring_tangent(dense, arcs, s)
            x, y = x - ty * lateral(t_acc), y + tx * lateral(t_acc)
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
