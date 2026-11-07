"""步频/步幅通道探针：统计 build_track 产出的步频、步幅(bdS)、总步数。"""
import math
import random
import sys

sys.path.insert(0, ".")
from sportsworld.domain.track import build_track  # noqa: E402

CX, CY = 113.5, 34.8          # 任意操场中心
MLAT, MLNG = 111000.0, 91000.0


def ring_pts(n=200, r=36.5, straight=80.0):
    """标准 400m 跑道近似环（BD 坐标）。"""
    pts = []
    half = straight / 2.0
    for i in range(n):
        t = i / n * 2 * math.pi
        x = r * math.sin(t)
        y = half * math.cos(t) if abs(math.cos(t)) > 0 else 0
        pts.append([CY + x / MLAT, CX + y / MLNG])
    return pts


fence = ring_pts()
for seed in (1, 99, 777, 2024):
    tr = build_track(3000, 900, seed, fence, fence_ring=fence,
                     start_ms=1700000000000)
    locs = [l for l in tr["locations"] if l["type"] != -1 and l["steps"] > 0]
    cads = []
    prev = None
    for l in tr["locations"]:
        if prev is not None and l["type"] != -1:
            dst = l["steps"] - prev["steps"]
            dt = l["totalTime"] - prev["totalTime"]
            if dt > 0:
                cads.append(dst / dt * 60)
        prev = l
    bds = [l["bdS"] for l in locs if l["bdS"] > 0]
    vs = [l["avgSpeed"] for l in locs if l["avgSpeed"] > 0]
    tots = tr["locations"][-1]
    mean_v = sum(vs) / len(vs)
    mean_str = tots["totalDis"] / max(1, tots["steps"])
    mean_cad = tots["steps"] / (tots["totalTime"] / 60.0)
    # bdS 隐含步频 = v/bdS*60
    impl = [v / s * 60 for v, s in zip(vs, bds) if s > 0]
    print(f"seed={seed} 点={len(tr['locations'])}")
    print(f"  总步数={tots['steps']} 总距={tots['totalDis']:.0f}m "
          f"总时={tots['totalTime']}s")
    print(f"  平均步频(总步数/总时)={mean_cad:.0f} spm  "
          f"点间步频 min={min(cads):.0f} max={max(cads):.0f} "
          f"mean={sum(cads)/len(cads):.0f}")
    print(f"  平均步幅(距/步)={mean_str:.2f}m  "
          f"bdS范围=[{min(bds):.2f},{max(bds):.2f}] "
          f"mean={sum(bds)/len(bds):.2f}")
    print(f"  bdS隐含步频=[{min(impl):.0f},{max(impl):.0f}] "
          f"mean={sum(impl)/len(impl):.0f} spm  平均速度={mean_v:.2f}m/s")
    print(f"  maxStepFreq={tr.get('maxStepFreq')} "
          f"maxStride={tr.get('maxStride')} "
          f"avgStepFreq={tr.get('avgStepFreq')}")
