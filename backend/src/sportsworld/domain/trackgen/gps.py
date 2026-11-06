"""GPS 通道：点型序列、漂移步长、定位半径/状态、AR(1) 抖动、瞬时速度毛刺。"""

import random

from ...config import CFG
from .util import weighted

_T = CFG.get("track") or {}
MAX_DRIFT = _T.get("max_drift", 2.5)   # 漂移点最大跳变（米）：跑者基本在 1 道内
MAX_DRIFT_GPS_KMH = _T.get("drift_gps_speed_kmh", 6.0)
GPS_ZERO_P = _T.get("gps_zero_p", 0.20)        # 正常点 GPS 速度 20% 概率为 0
GPS_SPIKE_P = _T.get("gps_spike_p", 0.015)     # GPS 瞬移毛刺概率（速度冲到 16-55 km/h）


def make_kinds(rng: random.Random, n):
    """点型 (type, locType) 序列：正常/暂停/漂移及其邻接修正。"""
    tl_w = [((-1, 4), 54), ((-1, 1), 27), ((-1, 12), 13), ((-1, 5), 3), ((-1, 6), 2)]
    p1, p2, p3 = _T.get("ptype_cum", [0.39, 0.93, 0.96])
    kinds = []
    for _ in range(n):
        u = rng.random()
        if u < p1:
            kinds.append((3, 1))
        elif u < p2:
            kinds.append((0, 1))
        elif u < p3:
            kinds.append(weighted(rng, tl_w))
        else:
            kinds.append((rng.choice([1, 1, 1, 1, 2, 2]), 1))
    for i in range(1, n):
        prev_drift = kinds[i - 1][0] in (-1, 5, 6)
        if kinds[i][0] != -1 and prev_drift and rng.random() < 0.08:
            kinds[i] = (7 if rng.random() < 0.75 else 8, 1)
    for i in range(1, n):
        if kinds[i][0] == -1 and kinds[i - 1][0] == -1:
            kinds[i] = (rng.choice([3, 0]), 1)
    return kinds


def drift_step(rng: random.Random):
    """漂移点步长：20% 原地、3% 大幅瞬移(8-22m)、其余 0~MAX_DRIFT。"""
    u = rng.random()
    return (0.0 if u < 0.20
            else rng.uniform(8.0, 22.0) if u < 0.23
            else rng.uniform(0.0, MAX_DRIFT))


def jitter_step(rng: random.Random, j):
    """AR(1) 位置抖动迭代一步（稳态 std ≈0.5m）。"""
    return 0.72 * j + rng.gauss(0.0, _T.get("ar1_sigma", 0.35))


def radius_state(rng: random.Random, typ):
    """正常点：定位半径 + 状态抽样。"""
    rad = round(rng.uniform(*_T.get("radius_typ3", [1.4, 3.0]))
                if typ == 3 else rng.uniform(*_T.get("radius_normal", [1.2, 2.0])), 2)
    state = weighted(rng, [(1, 145), (2, 45), (3, 164)] if typ == 0
                     else [(1, 145), (2, 256), (3, 151)])
    return rad, state


def radius_state_drift(rng: random.Random):
    """漂移点：定位半径 + 状态抽样。"""
    rad = round(rng.uniform(*_T.get("radius_drift", [1.6, 5.0])), 2)
    state = weighted(rng, [(1, 102), (2, 124), (3, 136)])
    return rad, state


def gps_speed(rng: random.Random, kmh):
    """正常点瞬时速度：20% 置零 / 1.5% 瞬移毛刺(16-55km/h) / 其余带噪。"""
    u = rng.random()
    if u < GPS_ZERO_P:
        return 0.0
    if u < GPS_ZERO_P + GPS_SPIKE_P:
        return round(rng.uniform(16.0, 55.0), 4)
    return round(max(0.0, kmh + rng.gauss(0.0, max(0.05, kmh * 0.08))), 4)


def gps_speed_drift(rng: random.Random):
    """漂移点瞬时速度。"""
    return round(rng.uniform(0.5, MAX_DRIFT_GPS_KMH), 4)
