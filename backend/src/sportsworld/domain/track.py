"""轨迹生成（精简移植 Rust src/track/generator.rs 的 Legacy 模式）。

沿给定闭合环（体育场拟合环或 Catmull-Rom 点环）按弧长推进，
叠加速度画像（起步/疲劳/三正弦/凹陷/微噪）、侧向摆动、
AR(1) GPS 抖动与异常点模拟，最后做打卡点 40m 吸附与围栏回拉。
"""

import math
import random

from ..config import CFG
from .geom import (MET_LAT, MET_LNG, clamp_to_ring, make_point_ring,
                   ring_point_at, ring_tangent)

_T = CFG.get("track") or {}
SPEED_FLOOR = _T.get("speed_floor", 1.90)
SPEED_CEIL = _T.get("speed_ceil", 6.30)
MAX_DRIFT = _T.get("max_drift", 2.5)   # 漂移点最大跳变（米）：跑者基本在 1 道内


def _weighted(rng: random.Random, items):
    total = sum(w for _, w in items)
    u = rng.uniform(0, total)
    for v, w in items:
        u -= w
        if u < 0:
            return v
    return items[-1][0]


def fit_speeds(w, dts, target):
    """等比缩放速度序列，钳在配速窗口内并命中目标总距。"""
    for _ in range(24):
        cur = sum(x * dt for x, dt in zip(w, dts))
        if abs(cur - target) <= 1.0:
            break
        k = target / cur
        w = [min(max(x * k, SPEED_FLOOR), SPEED_CEIL) for x in w]
    return [min(max(x, SPEED_FLOOR), SPEED_CEIL) for x in w]


def build_track(dist, dur, seed, points_bd, fence_ring=None, ring=None):
    """返回 [{gLat,gLng,totalTime,totalDis,ptype,speed,...}]。

    ring=(dense,arcs,center) 可注入自定义环（如体育场拟合环）；
    None 时用打卡点 Catmull-Rom 环。
    """
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
    _dt_main_p = _T.get("dt_main_p", 0.80)
    _dt_main = _T.get("dt_main", 5.0)
    _dt_others = _T.get("dt_others", [1.0, 2.0, 3.0, 4.0, 6.0, 7.0, 8.0])
    while t < dur_f:
        times.append(t)
        t += _dt_main if rng.random() < _dt_main_p else rng.choice(_dt_others)
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
    # （幅度按"大部分时间在 1 道内"取：合成摆动 ~±1m 以内）
    wv_a1, wv_t1 = rng.uniform(*_T.get("lateral_a1", [0.35, 0.8])), rng.uniform(70.0, 130.0)
    wv_a2, wv_t2 = rng.uniform(*_T.get("lateral_a2", [0.10, 0.3])), rng.uniform(16.0, 30.0)
    wv_p1, wv_p2 = rng.uniform(0.0, math.tau), rng.uniform(0.0, math.tau)

    def lateral(tt):
        return (wv_a1 * math.sin(math.tau * tt / wv_t1 + wv_p1)
                + wv_a2 * math.sin(math.tau * tt / wv_t2 + wv_p2))

    base = dist / dur_f
    # 起步加速段 / 末段减速段：真实 GPS 轨迹两端的速度渐变
    warmup = min(_T.get("warmup_s", 22.0), dur_f * 0.04)
    cooldown = min(_T.get("cooldown_s", 16.0), dur_f * 0.03)
    w = []
    for tt in times:
        if tt < warmup:
            ramp = 0.30 + 0.70 * (tt / warmup)           # 静止起步 → 巡航
        elif tt > dur_f - cooldown:
            ramp = 0.40 + 0.60 * ((dur_f - tt) / cooldown)  # 巡航 → 停表
        else:
            ramp = 1.0
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
    _p1, _p2, _p3 = _T.get("ptype_cum", [0.39, 0.93, 0.96])
    kinds = []
    for _ in range(n):
        u = rng.random()
        if u < _p1:
            kinds.append((3, 1))
        elif u < _p2:
            kinds.append((0, 1))
        elif u < _p3:
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
            _sig = _T.get("ar1_sigma", 0.35)
            jx = 0.72 * jx + rng.gauss(0.0, _sig)   # AR(1) 稳态 std ≈0.5m
            jy = 0.72 * jy + rng.gauss(0.0, _sig)
            px, py = x - ty * lo + jx, y + tx * lo + jy
            rad = round(rng.uniform(*_T.get("radius_typ3", [1.4, 3.0]))
                        if typ == 3 else rng.uniform(*_T.get("radius_normal", [1.2, 2.0])), 2)
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
            rad = round(rng.uniform(*_T.get("radius_drift", [1.6, 5.0])), 2)
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
    for plat, plng in points_bd:
        best_i, best_d = None, 1e18
        for i, q in enumerate(locs):
            if q["ptype"] == -1:
                continue
            dd = ((q["gLat"] - plat) * MET_LAT) ** 2 + ((q["gLng"] - plng) * MET_LNG) ** 2
            if dd < best_d:
                best_i, best_d = i, dd
        if best_i is not None and best_d < _T.get("snap_tolerance_m", 40.0) ** 2:
            locs[best_i]["gLat"], locs[best_i]["gLng"] = plat, plng
            locs[best_i]["snapped"] = True
    clamp_to_ring(locs, fence_ring)
    return locs
