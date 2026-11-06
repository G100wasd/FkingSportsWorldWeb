"""统计通道：10s 窗口/60s 分段重建 + 官方衍生指标汇总。"""

from ...config import CFG
from .altitude import total_ascent
from .speed import pace_milli

_T = CFG.get("track") or {}
WEIGHT_KG = _T.get("weight_kg", 68.0)          # 官方卡路里/功率公式的体重


def counters_at(locs, t):
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


def rebuild(track):
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
        lo, prev = 0, counters_at(locs, 0)
        while lo < track["totalTime"]:
            hi = min(lo + width, track["totalTime"])
            cur = counters_at(locs, hi)
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

def official_kcal(weight, total_time, total_dis):
    km = total_dis / 1000.0
    if km <= 0.0:
        met = 12.0
    else:
        pace = (total_time / 60.0) / km          # 分/km
        met = (33.0 / pace + 4.1) if pace < 10 else \
              (102.0 / pace - 3.7) if pace < 12 else (33.6 / pace + 1.0)
    return round(weight * round(met * 10) / 10.0 * (total_time / 3600.0))


def avg_power(weight, total_dis, total_time):
    return 0 if total_time <= 0 else round(1.1 * weight * (total_dis / total_time))


def finalize(track):
    """重建窗口/分段并汇总衍生指标（爬升/卡路里/功率/步频/配速）。"""
    rebuild(track)
    locs = track["locations"]
    track["totalAscent"] = round(total_ascent(locs))
    track["calorie"] = official_kcal(WEIGHT_KG, track["totalTime"],
                                     track["totalDistance"])
    track["avgPower"] = avg_power(WEIGHT_KG, track["totalDistance"],
                                  track["totalTime"])
    track["avgStepFreq"] = max(1, round(track["totalSteps"]
                                        / max(1, track["totalTime"]) * 60.0))
    track["paceMilli"] = pace_milli(track["totalTime"], track["totalDistance"])
    return track
