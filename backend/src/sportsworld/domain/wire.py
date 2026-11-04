"""统计序列的协议形态（移植 track/wire.rs + submit.rs::submit_windows）。

build_windows: 10s 窗 OBS 版（id=(rrid%100000)*1000+窗口末秒，queueNum=0）
submit_windows: 10s 窗提交版（id=60000+i，queueNum=i；指标同源）
build_laps: 每 1000m 一圈（avgPace 秒/km、avgStride 厘米、末圈 isFullLap=false）
five_point_*: 五点打卡回执（提交体 fivePointJson / OBS fixed_point_json 同源）
"""

import json
import math

from ..services import geo   # wire.rs 同样跨层用 api::fence::point_xy

MET_LAT = 111_132.0


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


# ── 五点打卡回执（wire.rs::passes_point / five_point_*）──────────

def passes_point(track, point, tol=40.0):
    """轨迹是否在 tol 米内经过打卡点（BD-09，排除异常点 type=-1）。"""
    cos_lat = 111_320.0 * math.cos(math.radians(point[0]))
    for p in track["locations"]:
        if p["type"] == -1:
            continue
        dy = (p["gLat"] - point[0]) * MET_LAT
        dx = (p["gLng"] - point[1]) * cos_lat
        if dx * dx + dy * dy <= tol * tol:
            return True
    return False


def five_point_payload(points, track):
    """逐点位回执对象（isPass 由轨迹判定；glat/glon 为 GCJ-02）。"""
    out = []
    for i, p in enumerate(points):
        bd = geo.point_xy(p)
        lat, lon = bd or (0.0, 0.0)
        glat, glon = geo.bd09_to_gcj02(lat, lon) if bd else (0.0, 0.0)
        out.append({
            "flag": track["startTime"], "glat": glat, "glon": glon,
            "id": p.get("id", i + 1),
            "isFixed": p.get("isFixed", 0),
            "isPass": passes_point(track, bd) if bd else False,
            "lat": lat, "lon": lon,
            "pointName": p.get("pointName") or "",
            "position": p.get("position", 999),
            "state": 0,
        })
    return out


def five_point_wrapper(points, track, run_area_id=-1, geo_fences=None,
                       freedom_show_fence=False):
    """五点回执 JSON 字符串（内外双层编码；提交体与 OBS 复用同一份）。"""
    _compact = dict(separators=(",", ":"), ensure_ascii=False)
    outer = {
        "useZip": False,
        "fivePointJson": json.dumps(five_point_payload(points, track),
                                    **_compact),
        "runAreaId": run_area_id,
        "geoFencesJson": json.dumps(geo_fences or [], **_compact),
        "freedomShowFence": freedom_show_fence,
    }
    return json.dumps(outer, **_compact)


def validate_five_point_wrapper(wrapper):
    """提交前校验：五点数组非空、坐标非零（wire.rs 同语义）。"""
    try:
        outer = json.loads(wrapper)
        pts = json.loads(outer["fivePointJson"])
    except (TypeError, ValueError, KeyError) as e:
        raise ValueError(f"五点轨迹 JSON 无效: {e}")
    if not pts:
        raise ValueError("五点轨迹不能为空")
    for p in pts:
        if (not p.get("lat") and not p.get("lon")) or \
           (not p.get("glat") and not p.get("glon")):
            raise ValueError("五点轨迹包含缺失坐标")
    return True
