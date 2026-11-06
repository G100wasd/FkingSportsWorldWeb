"""打卡点通道：按序吸附、平滑重拟合、已过点弱吸附、围栏回拉、尖角重拟合。"""

import math

from ...config import CFG
from ..geom import MET_LAT, MET_LNG, clamp_to_ring

_T = CFG.get("track") or {}


def cr_seg(p0, p1, p2, p3, t):
    """Catmull-Rom 单段插值（米制 (x,y)），曲线过 p1→p2。"""
    t2, t3 = t * t, t * t * t
    return tuple(
        0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t
               + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
               + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3)
        for k in (0, 1))


def corner_angle(a, b, c):
    """顶点 b 处轨迹内角（度）：向量 b→a 与 b→c 的夹角。
    直线=180°，越小越尖锐（发卡弯趋向 0°）。"""
    ux = (a["gLng"] - b["gLng"]) * MET_LNG
    uy = (a["gLat"] - b["gLat"]) * MET_LAT
    vx = (c["gLng"] - b["gLng"]) * MET_LNG
    vy = (c["gLat"] - b["gLat"]) * MET_LAT
    lu, lv = math.hypot(ux, uy), math.hypot(vx, vy)
    if lu < 1e-9 or lv < 1e-9:
        return 180.0
    cosv = max(-1.0, min(1.0, (ux * vx + uy * vy) / (lu * lv)))
    return math.degrees(math.acos(cosv))


def apply_snapping(locs, points_bd, fence_ring):
    """打卡点全流程：按序吸附 → 平滑重拟合 → 已过点弱吸附 →
    围栏回拉 → 尖角重拟合（顺序即原实现顺序）。"""
    # 点位吸附：按下发顺序 1->5 依次吸附，下标严格递增（保证按序经过）。
    # 每个打卡点取"上一次吸附之后首次进入容差窗口"内的最近正常点，
    # 即该点在轨迹上的第一次靠近（首个窗口 argmin）。
    tol2 = _T.get("snap_tolerance_m", 40.0) ** 2
    sw = int(_T.get("snap_smooth_halfwin", 4))
    last_i = -1
    snapped_pts = []
    lo_b, hi_b = sw + 1, len(locs) - 2 - sw   # 吸附点两侧须留得下平滑窗
    for plat, plng in points_bd:
        best_i, best_d, inside = None, 1e18, False
        win = []
        for i in range(last_i + 1, len(locs)):
            q = locs[i]
            if q["type"] == -1:
                continue
            dd = ((q["gLat"] - plat) * MET_LAT) ** 2 + \
                 ((q["gLng"] - plng) * MET_LNG) ** 2
            if dd < tol2:
                win.append(i)
                if dd < best_d:
                    best_i, best_d, inside = i, dd, True
            elif inside:
                break            # 离开首个容差窗口：本次靠近的最近点已定
        if best_i is not None and lo_b <= hi_b \
                and not (lo_b <= best_i <= hi_b):
            alt = [i for i in win if lo_b <= i <= hi_b]
            if alt:
                best_i = min(alt, key=lambda i: abs(i - best_i))
        if best_i is not None:
            q = locs[best_i]
            # 按需移动：协议判定点只需轨迹进入 snap_tolerance 容差，
            # 精确吸附会让离环远的点把轨迹拽出很深的绕行、再被尖角
            # 重拟合拉成横穿直线。只挪到容差内 margin 处即止：
            # d<=tol-margin 时仍精确吸附，更深点位截断为浅绕行
            tol_m = _T.get("snap_tolerance_m", 40.0)
            margin = _T.get("snap_margin_m", 10.0)
            d_cp = math.hypot((q["gLat"] - plat) * MET_LAT,
                              (q["gLng"] - plng) * MET_LNG)
            k = 1.0 if d_cp <= tol_m - margin or d_cp <= 0 \
                else (d_cp - (tol_m - margin)) / d_cp
            q["gLat"] = round(q["gLat"] + (plat - q["gLat"]) * k, 7)
            q["gLng"] = round(q["gLng"] + (plng - q["gLng"]) * k, 7)
            q["snapped"] = True
            last_i = best_i
            # 存最终吸附位置（截断后）：重拟合/弱吸附以真实顶点为目标
            snapped_pts.append((best_i, q["gLat"], q["gLng"]))

    # 吸附点平滑重拟合：打卡点偏离轨迹线时，单点硬吸附会留下针状外凸。
    # 以打卡点为中锚、两侧窗口端点为边界，用 Catmull-Rom 把该段重采样成
    # 过打卡点的平滑弧线（必过保留，视觉上是"跑出去打点再绕回"的自然弯）。
    if snapped_pts and sw > 0:
        snap_set = {i for i, _, _ in snapped_pts}

        def _xy(i):
            q = locs[i]
            return q["gLng"] * MET_LNG, q["gLat"] * MET_LAT

        for snap_i, plat, plng in snapped_pts:
            a_i = max(0, snap_i - sw)
            while a_i in snap_set and a_i < snap_i:
                a_i += 1
            b_i = min(len(locs) - 1, snap_i + sw)
            while b_i in snap_set and b_i > snap_i:
                b_i -= 1
            if a_i >= snap_i or b_i <= snap_i:
                continue
            A = _xy(a_i)
            A0 = _xy(a_i - 1) if a_i > 0 else A
            B = _xy(b_i)
            B1 = _xy(b_i + 1) if b_i < len(locs) - 1 else B
            T_ = (plng * MET_LNG, plat * MET_LAT)
            for j in range(a_i + 1, b_i):
                if j == snap_i or j in snap_set:
                    continue
                q = locs[j]
                if q["type"] == -1:
                    continue
                if j < snap_i:   # A→打卡点 弧段
                    t = (j - a_i) / (snap_i - a_i)
                    x, y = cr_seg(A0, A, T_, B, t)
                else:            # 打卡点→B 弧段
                    t = (j - snap_i) / (b_i - snap_i)
                    x, y = cr_seg(A, T_, B, B1, t)
                q["gLng"] = round(x / MET_LNG, 7)
                q["gLat"] = round(y / MET_LAT, 7)

    # 已过点弱吸附：打卡点被通过后吸引力降为 snap_post_weight——
    # 后续圈再次接近时，按高斯衰减向点位收缩（靠近但不保证重合，
    # 更贴近真实 GPS 轨迹"扫过点附近"的形态）。
    post_w = _T.get("snap_post_weight", 0.5)
    post_s2 = 2.0 * (_T.get("snap_post_sigma_m", 12.0) ** 2)
    for snap_i, plat, plng in snapped_pts:
        for i in range(snap_i + 1, len(locs)):
            q = locs[i]
            if q["type"] == -1:
                continue
            dy = (q["gLat"] - plat) * MET_LAT
            dx = (q["gLng"] - plng) * MET_LNG
            wgt = post_w * math.exp(-(dx * dx + dy * dy) / post_s2)
            if wgt < 0.01:
                continue
            q["gLat"] = round(q["gLat"] - wgt * dy / MET_LAT, 7)
            q["gLng"] = round(q["gLng"] - wgt * dx / MET_LNG, 7)
    clamp_to_ring(locs, fence_ring)

    # 尖角重拟合（最后一步，吸附/回拉之后统一收尾）：顶点内角
    # <= corner_min_deg 判定为发卡尖角，该点连同两侧邻点向各自
    # 弦中点收缩重拟合该段，迭代收敛；打卡吸附点钉住不动，
    # 其尖角改为只重拟合邻点来拉直。
    ang_min = _T.get("corner_min_deg", 100.0)
    ang_k = _T.get("corner_blend", 0.65)
    for _ in range(_T.get("corner_iters", 8)):
        sharp = [i for i in range(1, len(locs) - 1)
                 if corner_angle(locs[i - 1], locs[i], locs[i + 1]) <= ang_min]
        if not sharp:
            break
        fix = set()
        for i in sharp:
            for j in (i - 1, i, i + 1):
                if 0 <= j < len(locs) and not locs[j].get("snapped"):
                    fix.add(j)
        for j in fix:
            if 0 < j < len(locs) - 1:
                tx = (locs[j - 1]["gLng"] + locs[j + 1]["gLng"]) / 2.0
                ty = (locs[j - 1]["gLat"] + locs[j + 1]["gLat"]) / 2.0
            elif j == 0 and len(locs) > 2:      # 端点：沿 i1→i2 延长线
                tx = 2.0 * locs[1]["gLng"] - locs[2]["gLng"]
                ty = 2.0 * locs[1]["gLat"] - locs[2]["gLat"]
            elif len(locs) > 2:                  # 端点：沿 i_{-2}→i_{-3} 延长线
                tx = 2.0 * locs[-2]["gLng"] - locs[-3]["gLng"]
                ty = 2.0 * locs[-2]["gLat"] - locs[-3]["gLat"]
            else:
                continue
            q = locs[j]
            q["gLng"] = round(q["gLng"] + (tx - q["gLng"]) * ang_k, 7)
            q["gLat"] = round(q["gLat"] + (ty - q["gLat"]) * ang_k, 7)
