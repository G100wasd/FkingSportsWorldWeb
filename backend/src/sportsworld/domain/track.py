"""轨迹生成（精简移植 Rust src/track/generator.rs + postfix.rs + stats.rs）。

沿给定闭合环（体育场拟合环或 Catmull-Rom 点环）按弧长推进，
叠加速度画像（起步/疲劳/三正弦/凹陷/微噪）、侧向摆动、
AR(1) GPS 抖动与异常点模拟，最后做打卡点 40m 吸附与围栏回拉。

点位字段与协议 GenPoint 28 键一致；Track 顶层含窗口/分段统计。
"""

import math
import random
from datetime import datetime, timedelta, timezone

from ..config import CFG
from .geom import (MET_LAT, MET_LNG, clamp_to_ring, make_point_ring,
                   ring_point_at, ring_tangent)

_T = CFG.get("track") or {}
SPEED_FLOOR = _T.get("speed_floor", 1.90)
SPEED_CEIL = _T.get("speed_ceil", 6.30)
MAX_DRIFT = _T.get("max_drift", 2.5)   # 漂移点最大跳变（米）：跑者基本在 1 道内
MAX_DRIFT_GPS_KMH = _T.get("drift_gps_speed_kmh", 6.0)
GPS_ZERO_P = _T.get("gps_zero_p", 0.20)        # 正常点 GPS 速度 20% 概率为 0
WEIGHT_KG = _T.get("weight_kg", 68.0)          # 官方卡路里/功率公式的体重
ALT_BASE = _T.get("alt_base", 82.0)            # 海拔均值回归基准（米）
ALT_SIGMA_RANGE = _T.get("alt_sigma", [3.8, 6.2])
CADENCE_RANGE = _T.get("cadence_range", [100, 200])   # 步频 clamp 区间（步/分）

_CST = timezone(timedelta(hours=8))


def _fmt_gain_time(ms):
    return datetime.fromtimestamp(ms / 1000.0, _CST).strftime("%Y-%m-%d %H:%M:%S")


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


def _post_fixes(locs, rng, start_ms):
    """哨兵点与收尾修正（移植 postfix.rs）。

    末点收尾减速；索引1 type=5 全零起点哨兵；末点 type=6；后半段
    67%→n-2 低频散布 type=1/2/8；索引2 avgSpeed 边界修正；首点全零
    且速度取首个非零值；最后统一回算 gainTime/gainTimeMs。
    """
    if len(locs) >= 4:
        stop = round(rng.uniform(2.0, 2.6), 4)   # 停表减速，钳在有效窗口内
        e = locs[-1]
        e["speed"] = round(stop * 3.6, 4)
        e["avgSpeed"] = stop
        e["bdS"] = round(rng.uniform(0.05, 0.15), 3)
    if len(locs) > 2:
        sp = locs[1]                             # 起点哨兵：全零累计 + state=1
        sp["type"], sp["state"], sp["locType"] = 5, 1, 1
        sp["radius"] = sp["accuracy"] = round(rng.uniform(2.0, 3.5), 2)
        sp["speed"] = sp["avgSpeed"] = sp["bdS"] = 0.0
        sp["totalDis"] = sp["validDis"] = 0.0
        sp["totalTime"] = sp["validTime"] = sp["steps"] = 0
        e = locs[-1]                             # 终点哨兵
        e["type"], e["locType"] = 6, 1
        e["radius"] = e["accuracy"] = round(rng.uniform(1.5, 3.0), 2)
        for i in range(int(len(locs) * 0.67), max(0, len(locs) - 2)):
            if locs[i]["type"] != -1 and rng.random() < 0.06:
                locs[i]["type"] = rng.choice([1, 1, 2, 8])
                locs[i]["locType"] = 1
    if len(locs) > 3 and locs[2]["type"] != -1:    # 索引2：首个真实点不跨哨兵
        p2 = locs[2]
        p2["avgSpeed"] = min(max(
            round(p2["totalDis"] / max(1.0, float(p2["totalTime"])), 4),
            SPEED_FLOOR), SPEED_CEIL)
        p2["speed"] = round(p2["avgSpeed"] * 3.6, 4)
    if locs:                                       # 首点 = 起点：全零累计
        p0 = locs[0]
        p0["totalTime"] = p0["validTime"] = 0
        p0["totalDis"] = p0["validDis"] = 0.0
        p0["steps"], p0["state"] = 0, 1
        p0["type"], p0["locType"] = rng.choice([0, 0, 0, 7]), 1
        p0["radius"] = p0["accuracy"] = round(rng.uniform(1.5, 3.0), 2)
        for j in range(1, len(locs)):            # 速度取首个非零值
            if locs[j]["avgSpeed"] > 0.0:
                p0["avgSpeed"], p0["bdS"] = locs[j]["avgSpeed"], locs[j]["bdS"]
                break
        if p0["speed"] == 0.0:
            p0["speed"] = round(rng.uniform(7.0, 13.0), 4)
    for p in locs:                                 # 时间戳最后统一回算
        p["gainTimeMs"] = start_ms + p["totalTime"] * 1000
        p["gainTime"] = _fmt_gain_time(p["gainTimeMs"])


def _counters_at(locs, t):
    """时刻 t 的累计 (里程m, 步数)，相邻点线性插值（移植 stats.rs）。"""
    if not locs:
        return 0.0, 0
    first = locs[0]
    if t <= first["totalTime"]:
        return first["totalDis"], first["steps"]
    for a, b in zip(locs, locs[1:]):
        if b["totalTime"] >= t and b["totalTime"] > a["totalTime"]:
            k = (t - a["totalTime"]) / (b["totalTime"] - a["totalTime"])
            return (a["totalDis"] + (b["totalDis"] - a["totalDis"]) * k,
                    round(a["steps"] + (b["steps"] - a["steps"]) * k))
    last = locs[-1]
    return last["totalDis"], last["steps"]


def _rebuild(track):
    """由逐点累计列重建 10s 窗口与 60s 分段（移植 stats.rs::rebuild）。"""
    locs = track["locations"]
    if not locs:
        return
    last = locs[-1]
    track["totalTime"] = last["totalTime"]
    track["totalDistance"] = last["totalDis"]
    track["totalSteps"] = last["steps"]
    track["validDistance"] = last["validDis"]
    track["validTime"] = last["validTime"]
    for width, is_seg in ((10, False), (60, True)):
        lo, prev = 0, _counters_at(locs, 0)
        while lo < track["totalTime"]:
            hi = min(lo + width, track["totalTime"])
            cur = _counters_at(locs, hi)
            dist, steps = cur[0] - prev[0], cur[1] - prev[1]
            if is_seg:
                track["segments"].append({
                    "totalTime": hi - lo,
                    "distance": round(cur[0]) - round(prev[0]),
                    "startTime": lo * 1000, "endTime": hi * 1000,
                    "avgSpeed": dist / (hi - lo),
                    "avgStep": round(steps * 60.0 / (hi - lo)),
                    "state": 0})
            else:
                track["speedPerTenSec"].append({"time": hi - lo, "value": dist})
                track["stepsPerTenSec"].append({"time": hi - lo, "value": float(steps)})
            prev, lo = cur, hi


# ── 官方衍生指标（calorie.rs / submit.rs）────────────────────────

def _official_kcal(weight, total_time, total_dis):
    km = total_dis / 1000.0
    if km <= 0.0:
        met = 12.0
    else:
        pace = (total_time / 60.0) / km          # 分/km
        met = (33.0 / pace + 4.1) if pace < 10 else \
              (102.0 / pace - 3.7) if pace < 12 else (33.6 / pace + 1.0)
    return round(weight * round(met * 10) / 10.0 * (total_time / 3600.0))


def _avg_power(weight, total_dis, total_time):
    return 0 if total_time <= 0 else round(1.1 * weight * (total_dis / total_time))


def _total_ascent(locs):
    return sum(max(0.0, locs[i]["bdA"] - locs[i - 1]["bdA"])
               for i in range(1, len(locs)))


def _pace_milli(sec, meters):
    """记录 speed 字段：千分位 分/km。"""
    d = math.ceil(meters * 100.0) / 100.0
    return round(sec / 60.0 / (d / 1000.0) * 1000.0) if d > 0 else 0


def build_track(dist, dur, seed, points_bd, fence_ring=None, ring=None,
                start_ms=0):
    """返回 Track 字典：locations(28键协议点) + 顶层统计 + 衍生指标。

    ring=(dense,arcs,center) 可注入自定义环（如体育场拟合环）；
    None 时用打卡点 Catmull-Rom 环。start_ms 为伪装起跑毫秒时间戳，
    写入每点 flag/gainTime/gainTimeMs 与顶层 startTime。
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

    # 海拔 AR(1) 均值回归：σ 随点数反比缩放（统计特性对齐 Rust altitude 模型）
    alt = ALT_BASE + rng.uniform(-1.0, 1.0)
    n_est = max(1, int(dur_f / 5.0))
    alt_sigma = rng.uniform(*ALT_SIGMA_RANGE) / (0.40 * n_est)
    v_now = dist / dur_f                          # 步频模型用全程均速
    cad_lo, cad_hi = CADENCE_RANGE

    locs = []
    t_acc = dist_acc = steps_acc = 0.0
    jx = jy = 0.0
    for i in range(n):
        dt_i, (typ, lt) = dts[i], kinds[i]
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
            state = _weighted(rng, [(1, 145), (2, 45), (3, 164)] if typ == 0
                              else [(1, 145), (2, 256), (3, 151)])
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
            state = _weighted(rng, [(1, 102), (2, 124), (3, 136)])
        lat = c_lat + py / MET_LAT
        lng = c_lng + px / MET_LNG

        # 步频：步幅 = 0.62+0.17·v̄ + 慢波 + 微噪；步频 = v̄'/步幅 clamp [100,200]
        stride = (0.62 + 0.17 * v_now
                  + 0.03 * math.sin(math.tau * t_acc / 200.0 + phase_l)
                  + rng.gauss(0.0, 0.008))
        v_cad = v_now * (1.0 + 0.03 * math.sin(math.tau * t_acc / 70.0 + phase_v))
        cad = min(max(v_cad / stride * 60.0, cad_lo), cad_hi)
        steps_acc += cad / 60.0 * dt_i

        alt += 0.04 * (ALT_BASE - alt) + rng.gauss(0.0, alt_sigma)
        nxt = ring_point_at(dense, arcs, s + direction * 2.0)
        brg = (math.degrees(math.atan2(nxt[0] - x, nxt[1] - y))
               + rng.gauss(0.0, 35.0)) % 360.0

        # avgSpeed=位移/时长(m/s)（漂移点=累计均值）；speed=GPS瞬时速(km/h)
        if typ == -1:
            avg_sp = round(dist_acc / max(1.0, t_acc), 4)
            gps = round(rng.uniform(0.5, MAX_DRIFT_GPS_KMH), 4)
        else:
            avg_sp = round(disp[i] / dt_i, 4)
            kmh = avg_sp * 3.6
            gps = 0.0 if rng.random() < GPS_ZERO_P else round(
                max(0.0, kmh + rng.gauss(0.0, max(0.05, kmh * 0.08))), 4)

        locs.append({
            "id": i + 1, "flag": start_ms,
            "lat": -1.0, "lng": -1.0,
            "gLat": round(lat, 7), "gLng": round(lng, 7),
            "speed": gps, "avgSpeed": avg_sp,
            "radius": rad, "accuracy": rad,
            "type": typ, "locType": lt, "hasAltitude": True,
            "totalTime": round(t_acc), "totalDis": round(dist_acc, 4),
            "validDis": round(dist_acc, 4), "validTime": round(t_acc),
            "steps": int(steps_acc), "stepDistance": 0.0,
            "gainTime": _fmt_gain_time(start_ms + int(t_acc * 1000)),
            "gainTimeMs": start_ms + int(t_acc * 1000),
            "queueNum": 0, "coorType": "gcj02",
            "bdA": round(alt, 2), "bdD": round(brg, 2),
            "bdS": round(max(0.0, avg_sp * rng.uniform(0.6, 0.95)), 3),
            "bdG": rng.choice([1, 1, 1, -1]),
            "count": rng.randint(20, 88), "dtr": 0.0,
            "state": state, "locationId": "",
        })
    _post_fixes(locs, rng, start_ms)

    # 点位吸附：按下发顺序 1->5 依次吸附，下标严格递增（保证按序经过）。
    # 每个打卡点取"上一次吸附之后首次进入容差窗口"内的最近正常点，
    # 即该点在轨迹上的第一次靠近（首个窗口 argmin）。
    tol2 = _T.get("snap_tolerance_m", 40.0) ** 2
    last_i = -1
    for plat, plng in points_bd:
        best_i, best_d, inside = None, 1e18, False
        for i in range(last_i + 1, len(locs)):
            q = locs[i]
            if q["type"] == -1:
                continue
            dd = ((q["gLat"] - plat) * MET_LAT) ** 2 + \
                 ((q["gLng"] - plng) * MET_LNG) ** 2
            if dd < tol2:
                if dd < best_d:
                    best_i, best_d, inside = i, dd, True
            elif inside:
                break            # 离开首个容差窗口：本次靠近的最近点已定
        if best_i is not None:
            locs[best_i]["gLat"], locs[best_i]["gLng"] = plat, plng
            locs[best_i]["snapped"] = True
            last_i = best_i
    clamp_to_ring(locs, fence_ring)

    track = {
        "startTime": start_ms,
        "startLatitude": locs[0]["gLat"], "startLongitude": locs[0]["gLng"],
        "locations": locs,
        "speedPerTenSec": [], "stepsPerTenSec": [], "segments": [],
    }
    _rebuild(track)
    track["totalAscent"] = round(_total_ascent(locs))
    track["calorie"] = _official_kcal(WEIGHT_KG, track["totalTime"],
                                    track["totalDistance"])
    track["avgPower"] = _avg_power(WEIGHT_KG, track["totalDistance"],
                                   track["totalTime"])
    track["avgStepFreq"] = max(1, round(track["totalSteps"]
                                      / max(1, track["totalTime"]) * 60.0))
    track["paceMilli"] = _pace_milli(track["totalTime"], track["totalDistance"])
    return track
