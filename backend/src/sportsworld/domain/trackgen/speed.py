"""速度/配速通道：速度画像（起步/疲劳/三正弦/凹陷/停顿）、
总距拟合与记录级配速编码。"""

import math
import random

from ...config import CFG
from .util import weighted

_T = CFG.get("track") or {}
SPEED_FLOOR = _T.get("speed_floor", 0.55)      # 放宽到近似步行：停顿段需要 <1.9
SPEED_CEIL = _T.get("speed_ceil", 6.30)


def fit_speeds(w, dts, target, pinned=()):
    """等比缩放速度序列，钳在配速窗口内并命中目标总距。

    pinned: 不参与缩放的下标集合（停顿点）——它们的低速若被乘性
    补偿拉高，停顿特征会被拟合过程洗掉。"""
    pinned = set(pinned)
    for _ in range(24):
        cur = sum(x * dt for x, dt in zip(w, dts))
        if abs(cur - target) <= 1.0:
            break
        k = target / cur
        w = [x if i in pinned
             else min(max(x * k, SPEED_FLOOR), SPEED_CEIL)
             for i, x in enumerate(w)]
    return [x if i in pinned else min(max(x, SPEED_FLOOR), SPEED_CEIL)
            for i, x in enumerate(w)]


def make_dips(rng: random.Random, dur_f):
    """减速凹陷：大多为轻微掉速，偶发深坑（真实记录配速图普遍有 24'+ 尖刺）。
    返回 dip_factor(tt)。"""
    dips = [(rng.uniform(0.15, 0.75) * dur_f, rng.uniform(25.0, 55.0),
             weighted(rng, [(rng.uniform(0.08, 0.20), 55),
                            (rng.uniform(0.25, 0.55), 33),
                            (rng.uniform(0.60, 0.88), 12)]))
            for _ in range(rng.choice([1, 1, 2, 3]))]

    def dip_factor(tt):
        f = 1.0
        for c, hw, d in dips:
            if abs(tt - c) < hw:
                f *= 1.0 - d * 0.5 * (1.0 + math.cos(math.pi * (tt - c) / hw))
        return f

    return dip_factor


def make_pauses(rng: random.Random, dur_f):
    """显式停顿：0-2 段近矩形速度坍塌（系鞋带/喝水/看表，12-50s）。
    真记录的步频下限(6-96)与配速 24-48' 巨坑主要来源——
    窗口步数掉到 ~0-8、配速分段方差拉大。停顿在真记录中近乎必现。
    返回 pause_factor(tt)。"""
    pauses = [(rng.uniform(0.10, 0.85) * dur_f, rng.uniform(6.0, 25.0),
               rng.uniform(0.80, 0.97))
              for _ in range(rng.choice([0, 1, 1, 2]))]

    def pause_factor(tt):
        f = 1.0
        for c, hw, d in pauses:
            if abs(tt - c) < hw:
                f = min(f, 1.0 - d)
        return f

    return pause_factor


def base_profile(rng: random.Random, times, dur_f, dist, phase_v, dip_factor):
    """基础速度画像：起步加速/末段减速 + 疲劳衰减 + 三正弦波动 + 凹陷 + 微噪。"""
    base = dist / dur_f
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
                + 0.015 * math.sin(math.tau * tt / 19.0 + phase_v * 3.7)
                ) * dip_factor(tt)
        w.append(base * ramp * fatigue * wave * (1.0 + rng.gauss(0.0, 0.008)))
    return w


def apply_pauses(rng: random.Random, w, times, dts, dist, pause_factor):
    """停顿点先压成走/停速并钉住再拟合总距：乘性拟合补偿只能抬非停顿段，
    否则 fit_speeds 会把停顿下限一点点乘回巡航速度。
    返回 (w, pause_idx)。"""
    pause_idx = {i for i, tt in enumerate(times) if pause_factor(tt) < 0.97}
    for i in pause_idx:
        w[i] = min(w[i] * pause_factor(times[i]), rng.uniform(0.3, 0.9))
    return fit_speeds(w, dts, dist, pinned=pause_idx), pause_idx


def pace_milli(sec, meters):
    """记录 speed 字段：千分位 分/km。"""
    d = math.ceil(meters * 100.0) / 100.0
    return round(sec / 60.0 / (d / 1000.0) * 1000.0) if d > 0 else 0
