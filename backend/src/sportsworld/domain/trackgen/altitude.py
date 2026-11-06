"""海拔通道：分层模型序列生成 + 总爬升统计。"""

import random

import numpy as np
from scipy.interpolate import CubicSpline

from ...config import CFG

_T = CFG.get("track") or {}


def gen_altitudes(rng: random.Random, t_cum):
    """海拔序列（分层模型，对齐真实手机读数形态）：
      H0 地形趋势：稀疏控制点 + 三次样条（慢变基线）
      H1 中尺度波包：密集控制点(55-110s) + 三次样条——极值的主要来源，
         真实曲线的最低/最高点是波谷/波峰"荡"出来的，不是单点刺
      H2 小尺度扰动：逐点弱相关噪声（采样抖动）
      Hg GPS 误差：低概率尖刺（~30% 记录一根）+ 偶发小台阶跳变
    极差锚定 ~[14,24]m（真实平地记录普遍 10-26m），最高值 <= alt_cap。"""
    n = len(t_cum)
    T = max(float(t_cum[-1]), 60.0)
    ts = np.asarray(t_cum, dtype=float)

    n_cp = rng.randint(5, 7)                       # H0 地形趋势
    cp_t = [T * k / (n_cp - 1) for k in range(n_cp)]
    cp_a, acc = [0.0], 0.0
    for _ in range(n_cp - 1):
        acc += rng.uniform(-2.5, 2.5)
        cp_a.append(acc)
    h0 = CubicSpline(cp_t, cp_a)(ts)

    wp_t = [0.0]                                   # H1 中尺度波包
    while wp_t[-1] < T:
        wp_t.append(wp_t[-1] + rng.uniform(55.0, 110.0))
    wp_a = [rng.gauss(0.0, 2.4) for _ in wp_t]
    h1 = CubicSpline(wp_t, wp_a)(ts)

    h2 = np.convolve([rng.gauss(0.0, 0.85) for _ in range(n)],
                     [0.4, 0.6], "same")           # H2 逐点抖动

    hg = np.zeros(n)                               # Hg 尖刺/台阶跳
    if n > 24:
        if rng.random() < _T.get("alt_spike_p", 0.30):
            hg[rng.randint(2, n - 3)] += (rng.choice([-1.0, 1.0])
                                          * rng.uniform(4.5, 8.0))
        for _ in range(rng.randint(0, 2)):
            hg[rng.randint(0, n - 1)] += rng.gauss(0.0, 1.8)
        for _ in range(rng.randint(0, 2)):
            hg[rng.randint(10, n - 11):] += rng.uniform(-2.5, 2.5)

    dev = h0 + h1 + h2 + hg
    lo = 15.0 + rng.choice([-1.0, 1.0]) * rng.uniform(0.1, 0.9)
    hi = 23.0 + rng.choice([-1.0, 1.0]) * rng.uniform(0.1, 0.9)
    target = rng.uniform(min(lo, hi), max(lo, hi))
    r = float(dev.max() - dev.min())
    if r > 1e-6:
        dev = dev * (target / r)
    cap = _T.get("alt_cap", 55.5)                  # 最高海拔硬上限
    base = (cap - rng.uniform(0.0, 2.5)) - float(dev.max())
    return [base + float(v) for v in dev]


def total_ascent(locs):
    """爬升：平滑 + 之字形(zigzag)波段统计。
    波包模型下逐点差分几乎为零（平滑段）或全是噪声（锯齿段），
    真机的爬升来自确认过的上行波段——摆幅超过阈值才计入。"""
    a = [p["bdA"] for p in locs]
    n = len(a)
    if n < 3:
        return 0.0
    k = 5
    sm = [sum(a[max(0, i - k):min(n, i + k + 1)])
          / len(a[max(0, i - k):min(n, i + k + 1)]) for i in range(n)]
    swing = _T.get("ascent_min_swing", 2.5)
    asc, anchor, cand, up = 0.0, sm[0], sm[0], None
    for v in sm[1:]:
        if up is None:                       # 初始方向未定
            if v >= anchor + swing:
                up, cand = True, v
            elif v <= anchor - swing:
                up, cand = False, v
        elif up:
            if v > cand:
                cand = v
            elif cand - v >= swing:          # 波峰确认：收录上行段
                asc += cand - anchor
                anchor, cand, up = cand, v, False
        else:
            if v < cand:
                cand = v
            elif v - cand >= swing:          # 波谷确认：开启新上行段
                anchor, cand, up = cand, v, True
    if up:                                   # 收尾未确认的上行段
        asc += cand - anchor
    return asc
