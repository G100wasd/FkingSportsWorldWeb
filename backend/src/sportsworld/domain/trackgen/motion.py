"""跑者走位通道：侧向摆动 + 向内收缩扰动。"""

import math
import random

from ...config import CFG

_T = CFG.get("track") or {}


def make_lateral(rng: random.Random):
    """侧向摆动：两条低频正弦叠加，模拟跑者在道内的平滑走位
    （幅度按"大部分时间在 1 道内"取：合成摆动 ~±1m 以内）。"""
    a1, t1 = rng.uniform(*_T.get("lateral_a1", [0.35, 0.8])), rng.uniform(70.0, 130.0)
    a2, t2 = rng.uniform(*_T.get("lateral_a2", [0.10, 0.3])), rng.uniform(16.0, 30.0)
    p1, p2 = rng.uniform(0.0, math.tau), rng.uniform(0.0, math.tau)

    def lateral(tt):
        return (a1 * math.sin(math.tau * tt / t1 + p1)
                + a2 * math.sin(math.tau * tt / t2 + p2))

    return lateral


def make_inward(rng: random.Random):
    """向内收缩扰动：跑者倾向贴内道，向环心方向叠加一个 0~in_max 的
    慢变偏移（与侧向摆动独立，始终朝内不朝外）。"""
    a = rng.uniform(*_T.get("inward_a", [0.4, 1.2]))
    t = rng.uniform(*_T.get("inward_period_s", [80.0, 160.0]))
    p = rng.uniform(0.0, math.tau)

    def inward(tt):
        return a * (0.5 + 0.5 * math.sin(math.tau * tt / t + p))

    return inward
