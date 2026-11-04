"""控制台：登录 -> 确定跑步区域并拉取实时打卡点 -> 体育场跑道拟合 -> 轨迹生成 -> 可视化。

运行：python cli.py
输入账号密码（回车用 prompts.py 默认值），完成后 plt.show 弹出轨迹图，
进程退出时自动登出账号。
"""

import atexit
import math
import random

import matplotlib.pyplot as plt

from prompts import get_credentials
from sportsworld.domain import geom, stadium, track
from sportsworld.services import runarea
from sportsworld.services.auth import login

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

# 锚点（BD-09）：校区西侧坐标，服务端就近分配跑步区
ANCHOR_LAT = 22.9058142
ANCHOR_LON = 113.8709786
# 拉点时间戳：None=真实当前时间；伪造与声称跑步开始时间对齐时填毫秒值
POINTS_TS_MS = None

TRACK_DIST = 2250.0   # 米，2.25km ±0.25km
TRACK_DUR = 720       # 秒，均速 3.125 m/s ≈ 5'20"/km
TRACK_SEED = None     # None=每次随机；填整数可复现某次轨迹


def plot_result(area, fit, dense, locs, a_lat, a_lon):
    fig, ax = plt.subplots(figsize=(9, 9))
    for i, ring in enumerate(area["polygons"]):
        xs = [p[1] for p in ring] + [ring[0][1]]
        ys = [p[0] for p in ring] + [ring[0][0]]
        ax.fill(xs, ys, alpha=0.12)
        name = area["raws"][i].get("name") if i < len(area["raws"]) else None
        ax.plot(xs, ys, linewidth=1.2, label=name or f"fence{i}")
    bd = area["points_bd"]
    ax.scatter([p[1] for p in bd], [p[0] for p in bd],
               c="red", s=70, zorder=6, label="打卡点")
    # 拟合跑道轮廓（虚线）
    c = fit["cx_bd"]
    ax.plot([p[1] for p in fit["track_bd"]], [p[0] for p in fit["track_bd"]],
            "--", color="gray", linewidth=1.0, alpha=0.8, zorder=3, label="拟合跑道")
    # 参考中心 / 拟合中心 / 点到跑道最近点连线
    rc = area["ref_center"]
    ax.plot(rc[1], rc[0], "m+", markersize=14, markeredgewidth=2,
            zorder=7, label="参考中心")
    ax.plot(c[1], c[0], "c*", markersize=14, zorder=7, label="拟合中心")
    for (la, lo), (qx, qy) in zip(bd, fit["closest"]):
        ax.plot([lo, rc[1] + qx / geom.MET_LNG], [la, rc[0] + qy / geom.MET_LAT],
                ":", color="orange", linewidth=1.0, zorder=5)
    ax.plot([q["gLng"] for q in locs], [q["gLat"] for q in locs],
            "-", color="tab:blue", linewidth=1.0, alpha=0.8, zorder=4, label="拟合轨迹")
    ax.plot(locs[0]["gLng"], locs[0]["gLat"], "go", markersize=9, zorder=7, label="起点")
    ax.plot(locs[-1]["gLng"], locs[-1]["gLat"], "ks", markersize=9, zorder=7, label="终点")
    ax.plot(a_lon, a_lat, "k^", markersize=8, alpha=0.4, label="anchor")
    info = (f"L={fit['straight']:.0f}m r={fit['radius']:.0f}m "
            f"周长={fit['perimeter']:.0f}m\n"
            f"err: {' '.join(f'{e:.1f}' for e in fit['point_errors'])} "
            f"(max {fit['max_err']:.1f}m)\nmargin={fit['fence_margin']:.1f}m")
    ax.text(0.02, 0.02, info, transform=ax.transAxes, fontsize=8,
            va="bottom", bbox=dict(facecolor="white", alpha=0.7, edgecolor="gray"))
    ax.set_xlabel("longitude (BD-09)")
    ax.set_ylabel("latitude (BD-09)")
    ax.set_title(f"track fit  {TRACK_DIST:.0f}m / {TRACK_DUR}s, {len(locs)} pts")
    ax.legend()
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.3)
    return fig


def main() -> None:
    username, password = get_credentials()
    sess = login(username, password)
    atexit.register(sess.logout)

    print(f"[area] 锚点 BD=({ANCHOR_LAT:.6f},{ANCHOR_LON:.6f})")
    fences = runarea.fetch_fences(sess)
    area = runarea.fetch_area_points(
        sess, fences, ANCHOR_LAT, ANCHOR_LON, ts_ms=POINTS_TS_MS)
    for i, raw in enumerate(area["raws"]):
        meta = {k: v for k, v in raw.items() if k != "points"}
        mark = " <== 跑步区域" if i == area["stadium_idx"] else ""
        print(f"  fence{i}: {meta} 顶点={len(raw.get('points', []))}{mark}")
    for p, (la, lo) in zip(area["points"], area["points_bd"]):
        print(f"  {p.get('pointName', '')} BD=({la:.6f},{lo:.6f}) isFixed={p.get('isFixed')}")

    dense, arcs, center, fit = stadium.make_stadium_ring(
        area["points_bd"], area["ref_center"], area["stadium_ring"])
    fa = fit["fence"]
    print(f"[fence] 面积={fa['area']:.0f}m² 外接矩形={fa['rect'][3]:.0f}x{fa['rect'][4]:.0f}m "
          f"主方向={math.degrees(fa['angle']):.1f}° 长宽比={fa['aspect']:.2f}")
    print(f"[stadium] 中心=({fit['cx_bd'][0]:.6f},{fit['cx_bd'][1]:.6f}) "
          f"angle={math.degrees(fit['angle']):.1f}° 直道L={fit['straight']:.1f}m "
          f"半圆r={fit['radius']:.1f}m 总尺寸={fit['total_len']:.0f}x{fit['total_wid']:.0f}m "
          f"周长={fit['perimeter']:.0f}m")
    print(f"          误差(m): {' '.join(f'{e:.2f}' for e in fit['point_errors'])} "
          f"| 均={fit['mean_err']:.2f} 最大={fit['max_err']:.2f}")
    print(f"          边界余量={fit['fence_margin']:.1f}m(≥{fit['buffer']}) "
          f"包含=PASS 收敛={fit['converged']} 相近可行解={fit['alt_solutions']}"
          f"{' [尺寸不确定]' if fit['size_uncertain'] else ''}")

    seed = (TRACK_SEED if TRACK_SEED is not None
            else random.SystemRandom().randrange(2 ** 31))
    locs = track.build_track(TRACK_DIST, TRACK_DUR, seed, area["points_bd"],
                             fence_ring=area["stadium_ring"],
                             ring=(dense, arcs, center))
    inside = sum(1 for q in locs
                 if geom.in_ring(area["stadium_ring"], q["gLat"], q["gLng"]))
    snapped = sum(1 for q in locs if q.get("snapped"))
    print(f"[track] seed={seed} {len(locs)} 点 目标 {TRACK_DIST}m / {TRACK_DUR}s "
          f"累计={locs[-1]['totalDis']:.1f}m 场内点={inside}/{len(locs)} 吸附={snapped}")

    plot_result(area, fit, dense, locs, ANCHOR_LAT, ANCHOR_LON)
    plt.show()


if __name__ == "__main__":
    main()
