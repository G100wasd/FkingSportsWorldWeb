"""哨兵点与收尾修正通道（移植 postfix.rs）。"""

import random

from .speed import SPEED_CEIL, SPEED_FLOOR
from .util import fmt_gain_time


def post_fixes(locs, rng: random.Random, start_ms):
    """末点收尾减速；索引1 type=5 全零起点哨兵；末点 type=6；后半段
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
        p["gainTime"] = fmt_gain_time(p["gainTimeMs"])
