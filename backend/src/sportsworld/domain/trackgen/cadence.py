"""步频/步幅通道。"""

import math
import random

from ...config import CFG

_T = CFG.get("track") or {}
CADENCE_RANGE = _T.get("cadence_range", [0, 320])   # 真记录 min 普遍<100、max 可到 260


def cadence_at(rng: random.Random, v_prev, t_acc, phase_l, v_ref):
    """步频 = 瞬时速度/步幅——随 dips 自然掉落（停顿段 ~10-60），
    偶发毛刺上冲到 cad_max（真实上限 ~260）；步幅以 stride_mean 为
    均值随速度偏移缩放（快=大步幅），隐含均值≈配置值。"""
    lo, _ = CADENCE_RANGE
    hi = _T.get("cad_max", CADENCE_RANGE[1])   # 真实记录上限 ~260，运行期可改
    stride = (_T.get("stride_mean", 1.2) + 0.21 * (v_prev - v_ref)
              + 0.03 * math.sin(math.tau * t_acc / 200.0 + phase_l)
              + rng.gauss(0.0, 0.008))
    stride = max(0.35, stride)
    cad = v_prev / stride * 60.0
    if rng.random() < _T.get("cad_spike_p", 0.035):
        cad *= rng.uniform(1.35, 1.80)          # 传感器毛刺 → 200-260+
    return min(max(cad + rng.gauss(0.0, 3.0), lo), hi)
