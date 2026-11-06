"""合成验证：新生成器的派生指标是否贴近真实记录统计。

对照锚点（真跑截图）：
  步频 min<100 / max 可到 220-260；海拔波动 10-26m；
  配速有停顿坑与瞬移毛刺；公里分段方差大。
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from sportsworld.domain import geom, stadium, track, wire  # noqa: E402


def synth_area():
    """400m 场：直道 84m + 半圆 r36.5，5 打卡点散布。"""
    c_lat, c_lng = 22.90, 113.87
    L, R = 84.0, 36.5
    pts_xy = []
    # 参数化体育场轮廓
    def outline(u):  # u: 0..peri 沿环
        peri = 2 * L + 2 * math.pi * R
        u %= peri
        if u < L:                    # 上直道
            return (-L / 2 + u, R)
        u -= L
        if u < math.pi * R:          # 右半圆
            a = u / R
            return (L / 2 + R * math.sin(a), R * math.cos(a))
        u -= math.pi * R
        if u < L:                    # 下直道
            return (L / 2 - u, -R)
        u -= L
        a = u / R                    # 左半圆
        return (-L / 2 - R * math.sin(a), -R * math.cos(a))
    fence = []
    peri = 2 * L + 2 * math.pi * R
    for k in range(80):
        x, y = outline(k / 80 * peri)
        fence.append((c_lat + y / geom.MET_LAT, c_lng + x / geom.MET_LNG))
    cps = []
    for s in (60, 130, 200, 260, 330):
        x, y = outline(s)
        cps.append((c_lat + y / geom.MET_LAT, c_lng + x / geom.MET_LNG))
    return cps, fence


def stats(trk):
    locs = trk["locations"]
    alts = [p["bdA"] for p in locs]
    # 10s 窗口步频（近似 app 步频图）
    stf = [w["value"] * 6.0 for w in trk["stepsPerTenSec"] if w["time"] > 0]
    # 配速窗（10s 位移 → s/km）
    paces = [1000.0 / (w["value"] / w["time"]) for w in trk["speedPerTenSec"]
             if w["value"] > 0.01]
    laps = wire.build_laps(trk, trk["startTime"])
    jumps = []
    for a, b in zip(locs, locs[1:]):
        dx = (b["gLng"] - a["gLng"]) * geom.MET_LNG
        dy = (b["gLat"] - a["gLat"]) * geom.MET_LAT
        jumps.append(math.hypot(dx, dy))
    slow = sum(1 for p in locs if 0 < p["avgSpeed"] < 1.0)
    return {
        "alt_range": round(max(alts) - min(alts), 1),
        "cad_min": round(min(stf)), "cad_max": round(max(stf)),
        "cad_avg": trk["avgStepFreq"],
        "slow_pts": slow,
        "pace_min": round(min(paces)), "pace_max": round(max(paces)),
        "lap_paces": [round(l["avgPace"]) for l in laps if l["avgPace"]],
        "max_jump": round(max(jumps), 1),
        "steps": trk["totalSteps"], "ascent": trk["totalAscent"],
        "snapped": sum(1 for q in locs if q.get("snapped")),
    }


def main():
    cps, fence = synth_area()
    ring = stadium.make_stadium_ring(cps, (cps[0][0], cps[0][1]), fence)
    for seed in (7, 42, 1234):
        trk = track.build_track(2200, 720, seed, cps,
                                fence_ring=fence,
                                ring=(ring[0], ring[1], ring[2]),
                                start_ms=1_760_000_000_000)
        print(f"seed={seed}: {stats(trk)}")


if __name__ == "__main__":
    main()
