"""标准田径场跑道拟合：两直道 + 两半圆的 Stadium Shape。

词典序目标：围栏包含(硬约束) > 几何合理 > 贴合点位 > 靠近参考中心。
参数 (cx, cy, angle, straightLength, radius)，多初始值 SLSQP 约束优化。
"""

import math

import numpy as np

from .geom import (MET_LAT, MET_LNG, analyze_fence, poly_signed_dist)

STADIUM_BUFFER = 2.0   # 跑道与围栏边界的安全距离（m）

# 多目标权重
LAM_PTS = 1.0
LAM_SHAPE = 60.0
LAM_CENTER = 0.02
LAM_PRIOR = 0.05
HUBER_D = 5.0                          # 鲁棒核阈值（m），压低异常点影响
ASPECT_LO, ASPECT_HI = 1.55, 4.0       # (L+2r)/(2r) 软约束合理区间
ASPECT_HARD_LO, ASPECT_HARD_HI = 1.35, 5.5  # 硬约束区间
STD_PERIMETERS = (200.0, 250.0, 300.0, 350.0, 400.0)  # 常见跑道周长先验


def stadium_outline(cx, cy, ang, L, r, n):
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


def track_closest(cx, cy, ang, L, r, px, py):
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


def make_stadium_ring(bd_pts, center_ll, fence_ring_ll, n=720, buffer=STADIUM_BUFFER):
    """标准田径场拟合。

    输入 bd_pts=[(lat,lon),...]（打卡点）、center_ll=参考中心（仅初始化，
    不固定拟合中心）、fence_ring_ll=跑步区域围栏（硬约束边界）。
    返回 (dense, arcs, center_ll, result)；无可行解时抛 RuntimeError。
    """
    from scipy.optimize import minimize

    clat, clng = center_ll
    plane = np.array([[(lo - clng) * MET_LNG, (la - clat) * MET_LAT] for la, lo in bd_pts])
    fence_xy = [((lo - clng) * MET_LNG, (la - clat) * MET_LAT) for la, lo in fence_ring_ll]
    fx = analyze_fence(fence_xy)
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
        geo = [poly_signed_dist(fence_xy, x, y) - buffer
               for x, y in stadium_outline(*p, m)]
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
                margin = min(poly_signed_dist(fence_xy, x, y)
                             for x, y in stadium_outline(*p, n))
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

    dense = stadium_outline(cx, cy, ang, L, r, n)
    arcs = [0.0]
    for i in range(1, n + 1):
        a, b = dense[i - 1], dense[i % n]
        arcs.append(arcs[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    # 输出采样间隔 <=1m：不够则加密重建
    if arcs[-1] > n:
        n = int(arcs[-1]) + 1
        dense = stadium_outline(cx, cy, ang, L, r, n)
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
        "closest": [track_closest(cx, cy, ang, L, r, px, py) for px, py in plane],
    }
    return dense, arcs, (clat, clng), result
