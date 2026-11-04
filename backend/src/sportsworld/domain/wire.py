"""统计序列的协议形态（移植 track/wire.rs + submit.rs::submit_windows）。

build_windows: 10s 窗 OBS 版（id=(rrid%100000)*1000+窗口末秒，queueNum=0）
submit_windows: 10s 窗提交版（id=60000+i，queueNum=i；指标同源）
build_laps: 每 1000m 一圈（avgPace 秒/km、avgStride 厘米、末圈 isFullLap=false）
"""

import math


def _r0(x):
    """四舍五入到整数（半值远离零，与 Rust round 一致）。"""
    return int(math.floor(x + 0.5)) if x >= 0 else int(math.ceil(x - 0.5))


def build_windows(track, rrid=0):
    """10s 时间窗 OBS 形态。返回 (speed_windows, step_windows)。"""
    start_ms = track["startTime"]
    total = track["totalTime"]
    sp, stf, lo = [], [], 0
    for a, b in zip(track["speedPerTenSec"], track["stepsPerTenSec"]):
        hi = min(lo + a["time"], total)
        wid = (rrid % 100000) * 1000 + hi
        sp.append({
            "beginTime": start_ms + lo * 1000,
            "distance": a["value"],
            "endTime": start_ms + hi * 1000,
            "flag": start_ms, "id": wid, "queueNum": 0, "state": 0,
        })
        stf.append({
            "avgDiff": 0.0, "beginTime": start_ms + lo * 1000,
            "endTime": start_ms + hi * 1000,
            "flag": start_ms, "id": wid, "maxDiff": 0.0, "minDiff": 1000.0,
            "queueNum": 0, "state": 0, "stepsNum": int(b["value"]),
        })
        lo = hi
    return sp, stf


def submit_windows(track):
    """提交版窗口：id=60000+i、queueNum=i（其余字段与 OBS 版一致）。"""
    sp, stf = build_windows(track, 0)
    for vals in (sp, stf):
        for i, w in enumerate(vals):
            w["id"], w["queueNum"] = 60000 + i, i
    return sp, stf


def build_laps(track, start_ms):
    """每 1000m 一圈；末圈 isFullLap=false。边界处累计量线性插值。"""
    locs = track["locations"]
    if not locs:
        return []
    last = locs[-1]
    prev_d, prev_t, prev_steps, prev_gain = 0.0, 0, 0, 0.0
    alt0 = locs[0]["bdA"]
    laps = []
    while prev_d < last["totalDis"]:
        d_now = min(prev_d + 1000.0, last["totalDis"])
        gain, t_now, steps, altitude = (
            0.0, last["totalTime"], last["steps"], last["bdA"])
        for a, b in zip(locs, locs[1:]):
            climb = max(0.0, b["bdA"] - a["bdA"])
            if (d_now < last["totalDis"] and b["totalDis"] >= d_now
                    and b["totalDis"] > a["totalDis"]):
                k = (d_now - a["totalDis"]) / (b["totalDis"] - a["totalDis"])
                t_now = _r0(a["totalTime"] + (b["totalTime"] - a["totalTime"]) * k)
                steps = _r0(a["steps"] + (b["steps"] - a["steps"]) * k)
                altitude = a["bdA"] + (b["bdA"] - a["bdA"]) * k
                gain += climb * k
                break
            gain += climb
        lap_d = d_now - prev_d
        lap_t = t_now - prev_t
        lap_steps = steps - prev_steps
        laps.append({
            "avgCadence": round(lap_steps / (lap_t / 60.0), 2)
                          if lap_t > 0 else None,
            "avgPace": round(lap_t / (lap_d / 1000.0), 2) if lap_t > 0 else None,
            "avgStride": round(lap_d / lap_steps * 100.0, 2)
                         if lap_steps > 0 else None,
            "cumulativeDuration": t_now,
            "distance": round(lap_d, 4),
            "duration": lap_t,
            "elevationGain": round(gain - prev_gain, 2),
            "endAltAbs": round(altitude, 2),
            "endAltRel": round(altitude - alt0, 2),
            "flag": start_ms,
            "id": len(laps) + 1,
            "isFullLap": lap_d >= 1000.0,
            "lapIndex": len(laps) + 1,
            "step": lap_steps,
        })
        prev_d, prev_t, prev_steps, prev_gain = d_now, t_now, steps, gain
    return laps
