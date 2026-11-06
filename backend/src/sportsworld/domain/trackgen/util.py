"""公共小工具：加权抽样 / 不等间隔时间轴采样 / 时间戳格式化。"""

import random
from datetime import datetime, timedelta, timezone

from ...config import CFG

_T = CFG.get("track") or {}
_CST = timezone(timedelta(hours=8))


def fmt_gain_time(ms):
    return datetime.fromtimestamp(ms / 1000.0, _CST).strftime("%Y-%m-%d %H:%M:%S")


def weighted(rng: random.Random, items):
    total = sum(w for _, w in items)
    u = rng.uniform(0, total)
    for v, w in items:
        u -= w
        if u < 0:
            return v
    return items[-1][0]


def sample_times(rng: random.Random, dur_f):
    """不等间隔采样时刻：主间隔 dt_main(默认5s) 占 dt_main_p，其余 1-8s 散布。
    返回 (times, dts)。"""
    times, t = [], 0.0
    p = _T.get("dt_main_p", 0.80)
    main = _T.get("dt_main", 5.0)
    others = _T.get("dt_others", [1.0, 2.0, 3.0, 4.0, 6.0, 7.0, 8.0])
    while t < dur_f:
        times.append(t)
        t += main if rng.random() < p else rng.choice(others)
    dts = [times[i + 1] - times[i] for i in range(len(times) - 1)] \
        + [max(1.0, dur_f - times[-1])]
    return times, dts
