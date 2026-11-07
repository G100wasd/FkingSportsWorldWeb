"""轨迹生成管理器（精简移植 Rust src/track/generator.rs + postfix.rs + stats.rs）。

各提交数据通道的计算拆分在 trackgen 包中，本文件只负责编排汇总：

  trackgen.util      时间轴采样 / 加权抽样 / 时间戳
  trackgen.speed     速度画像（起步/疲劳/波动/凹陷/停顿）与总距拟合
  trackgen.motion    侧向摆动 / 向内收缩走位
  trackgen.gps       点型序列 / 漂移 / 定位半径与状态 / AR 抖动 / 瞬时速度
  trackgen.cadence   步频 / 步幅
  trackgen.altitude  海拔序列与爬升
  trackgen.postfix   哨兵点与收尾修正
  trackgen.snap      打卡点吸附 / 平滑重拟合 / 尖角重拟合
  trackgen.stats     窗口/分段重建与衍生指标

build_track 沿给定闭合环（体育场拟合环或 Catmull-Rom 点环）按弧长推进，
逐点汇总各通道输出为协议 GenPoint 28 键，最终产出 Track 字典送往前端渲染
与提交链路。
"""

import math
import random

from .geom import (MET_LAT, MET_LNG, make_point_ring,
                   ring_point_at, ring_tangent)
from .trackgen import (altitude, cadence, gps, motion, postfix,  # noqa: F401
                       snap, speed, stats, util)
from .trackgen.speed import fit_speeds  # noqa: F401  兼容旧引用
from .trackgen.util import fmt_gain_time


def build_track(dist, dur, seed, points_bd, fence_ring=None, ring=None,
                start_ms=0):
    """返回 Track 字典：locations(28键协议点) + 顶层统计 + 衍生指标。

    ring=(dense,arcs,center) 可注入自定义环（如体育场拟合环）；
    None 时用打卡点 Catmull-Rom 环。start_ms 为伪装起跑毫秒时间戳，
    写入每点 flag/gainTime/gainTimeMs 与顶层 startTime。
    """
    rng = random.Random(seed)
    dur_f = float(dur)
    if ring is None:
        dense, arcs, (c_lat, c_lng) = make_point_ring(points_bd)
    else:
        dense, arcs, (c_lat, c_lng) = ring
    # 行进方向必须匹配打卡点下发序：把点位投到环上取最近弧长，
    # 下发序的弧长走向(升/降多数票)即推进方向——否则吸附下标严格
    # 递增会把锚点摊到各圈，末个吸附落在轨迹尾部、平滑窗被边界
    # 截断，产生横穿场地的直线跳点。
    total_len = arcs[-1] or 400.0

    def _nearest_s(pt):
        px = (pt[1] - c_lng) * MET_LNG
        py = (pt[0] - c_lat) * MET_LAT
        bi, bs = 0, 1e18
        for k in range(len(dense)):
            dd = (dense[k][0] - px) ** 2 + (dense[k][1] - py) ** 2
            if dd < bs:
                bi, bs = k, dd
        return arcs[bi]

    ss = [_nearest_s(p) for p in points_bd] if points_bd else []
    # 带符号回绕差求和：跨弧长零点的相邻点对也按最短环向计，
    # 总和 >0 => 下发序沿 s 递增方向推进，反之递减方向
    tot = 0.0
    for a, b in zip(ss, ss[1:]):
        d = (b - a) % total_len
        tot += d - total_len if d > total_len / 2 else d
    direction = (1.0 if tot >= 0 else -1.0) if len(ss) > 1 \
        else rng.choice([1.0, -1.0])
    # 起跑点固定在第 1 个打卡点之前 lead 米：首个吸附下标落在
    # 平滑窗之后，首尾端部不再有"无点可平滑"的瞬移直线
    s = (ss[0] - direction * rng.uniform(80.0, 120.0)) % total_len \
        if ss else rng.uniform(0.0, total_len)
    phase_v = rng.uniform(0.0, math.tau)
    phase_l = rng.uniform(0.0, math.tau)

    # ── 各通道构建（顺序即原实现顺序，保持 RNG 序列稳定）──────
    times, dts = util.sample_times(rng, dur_f)
    n = len(times)
    dip_factor = speed.make_dips(rng, dur_f)
    pause_factor = speed.make_pauses(rng, dur_f)
    lateral = motion.make_lateral(rng)
    inward = motion.make_inward(rng)
    w = speed.base_profile(rng, times, dur_f, dist, phase_v, dip_factor)
    w, pause_idx = speed.apply_pauses(rng, w, times, dts, dist, pause_factor)
    seg = [w[i] * dts[i] for i in range(n)]
    kinds = gps.make_kinds(rng, n)

    # 位移重分配：漂移点/哨兵不产生位移，停顿里程不参与重分配
    normal = [i for i in range(n)
              if kinds[i][0] != -1 and i > 1 and i not in pause_idx]
    share = sum(seg[i] for i in normal) or 1.0
    rest = dist - sum(seg[i] for i in pause_idx)
    disp = [0.0] * n
    for i in normal:
        disp[i] = seg[i] * (rest / share)
    for i in pause_idx:
        disp[i] = seg[i]

    # 海拔：分层模型整段预生成（地形趋势+波包+抖动+低概率尖刺）
    _tc, _ta = [], 0.0
    for _d in dts:
        _ta += _d
        _tc.append(_ta)
    alts = altitude.gen_altitudes(rng, _tc)
    v_cad_prev = dist / dur_f

    # ── 主循环：逐点汇总各通道输出为 28 键协议点 ─────────────
    locs = []
    t_acc = dist_acc = steps_acc = 0.0
    jx = jy = 0.0
    for i in range(n):
        dt_i, (typ, lt) = dts[i], kinds[i]
        t_acc += dt_i
        if typ != -1:
            s += direction * disp[i]
            x, y = ring_point_at(dense, arcs, s)
            tx, ty = ring_tangent(dense, arcs, s)
            lo = lateral(t_acc)
            iw = inward(t_acc)
            rr = math.hypot(x, y) or 1.0
            jx = gps.jitter_step(rng, jx)
            jy = gps.jitter_step(rng, jy)
            px, py = x - ty * lo - (x / rr) * iw + jx, \
                     y + tx * lo - (y / rr) * iw + jy
            rad, state = gps.radius_state(rng, typ)
            dist_acc += disp[i]
        else:
            d_step = gps.drift_step(rng)
            x, y = ring_point_at(dense, arcs, s)
            tx, ty = ring_tangent(dense, arcs, s)
            iw = inward(t_acc)
            rr = math.hypot(x, y) or 1.0
            x, y = x - ty * lateral(t_acc) - (x / rr) * iw, \
                   y + tx * lateral(t_acc) - (y / rr) * iw
            if d_step > 0:
                ang = rng.uniform(0.0, math.tau)
                px, py = x + d_step * math.sin(ang), y + d_step * math.cos(ang)
            elif locs:
                px = (locs[-1]["gLng"] - c_lng) * MET_LNG
                py = (locs[-1]["gLat"] - c_lat) * MET_LAT
            else:
                px, py = x + jx, y + jy
            rad, state = gps.radius_state_drift(rng)
        lat = c_lat + py / MET_LAT
        lng = c_lng + px / MET_LNG

        # 步频/步数
        if typ != -1:
            v_cad_prev = disp[i] / dt_i
        cad = cadence.cadence_at(rng, v_cad_prev, t_acc, phase_l,
                                 dist / max(dur_f, 1.0))
        steps_acc += cad / 60.0 * dt_i

        alt_v = alts[i]
        nxt = ring_point_at(dense, arcs, s + direction * 2.0)
        brg = (math.degrees(math.atan2(nxt[0] - x, nxt[1] - y))
               + rng.gauss(0.0, 35.0)) % 360.0

        # avgSpeed=位移/时长(m/s)（漂移点=累计均值）；speed=GPS瞬时速(km/h)
        if typ == -1:
            avg_sp = round(dist_acc / max(1.0, t_acc), 4)
            gps_v = gps.gps_speed_drift(rng)
        else:
            avg_sp = round(disp[i] / dt_i, 4)
            gps_v = gps.gps_speed(rng, avg_sp * 3.6)

        locs.append({
            "id": i + 1, "flag": start_ms,
            "lat": -1.0, "lng": -1.0,
            "gLat": round(lat, 7), "gLng": round(lng, 7),
            "speed": gps_v, "avgSpeed": avg_sp,
            "radius": rad, "accuracy": rad,
            "type": typ, "locType": lt, "hasAltitude": True,
            "totalTime": round(t_acc), "totalDis": round(dist_acc, 4),
            "validDis": round(dist_acc, 4), "validTime": round(t_acc),
            "steps": int(steps_acc), "stepDistance": 0.0,
            "gainTime": fmt_gain_time(start_ms + int(t_acc * 1000)),
            "gainTimeMs": start_ms + int(t_acc * 1000),
            "queueNum": 0, "coorType": "gcj02",
            "bdA": round(alt_v, 2), "bdD": round(brg, 2),
            "bdS": round(max(0.0, avg_sp * rng.uniform(0.6, 0.95)), 3),
            "bdG": rng.choice([1, 1, 1, -1]),
            "count": rng.randint(20, 88), "dtr": 0.0,
            "state": state, "locationId": "",
        })
    postfix.post_fixes(locs, rng, start_ms)

    # 打卡点吸附/平滑/弱吸附/围栏回拉/尖角重拟合
    snap.apply_snapping(locs, points_bd, fence_ring)

    # 汇总：窗口/分段重建 + 衍生指标
    track = {
        "startTime": start_ms,
        "startLatitude": locs[0]["gLat"], "startLongitude": locs[0]["gLng"],
        "locations": locs,
        "speedPerTenSec": [], "stepsPerTenSec": [], "segments": [],
    }
    return stats.finalize(track)
