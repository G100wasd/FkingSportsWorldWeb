"""临时测试：登录 -> 拉围栏多边形+runAreaId -> 拉打卡点 -> matplotlib 可视化。

直接运行：python _fence_test.py
打卡点接口有服务端限流（5分钟3次），重复跑前稍微等一会。
"""

import matplotlib

matplotlib.use("Agg")  # 无窗口环境也能出图；想弹窗改成 plt.show()
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False

from sportsworld.services import geo
from sportsworld.services.auth import login

USERNAME = "18928206383"
PASSWORD = "20061030Cjj"

OUT_PNG = "fence_points.png"


def ring_centroid(ring: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(ring)
    return sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n


def main() -> None:
    sess = login(USERNAME, PASSWORD)
    try:
        fence = geo.fetch_geo_fence(sess)
        polys = fence["polygons"]
        run_area_id = fence["run_area_id"]
        print(f"[fence] runAreaId={run_area_id} polygons={len(polys)} "
              f"points={sum(len(r) for r in polys)}")
        for i, raw in enumerate(fence["raw"]):
            meta = {k: raw.get(k) for k in
                    ("campusId", "campusName", "fenceName", "name", "runAreaId", "fenceId", "type")
                    if raw.get(k) is not None}
            print(f"  fence{i}: {meta or '(无元数据)'} 顶点={len(raw.get('points', []))}")
        if not polys:
            print("[fence] 无围栏多边形，无法确定锚点，结束")
            return

        # 用顶点数最多的多边形质心做点位锚点
        biggest = max(polys, key=len)
        a_lat, a_lon = ring_centroid(biggest)
        print(f"[points] 锚点 BD=({a_lat:.6f},{a_lon:.6f})")
        pts = geo.fetch_points(sess, a_lat, a_lon, run_area_id)
        print(f"[points] {len(pts)} 个打卡点")
        for p in pts[:10]:
            xy = geo.point_xy(p)
            print(f"  {p.get('pointName','')} BD={xy} must={p.get('isMustPoint')}")

        # --- 可视化 ---
        fig, ax = plt.subplots(figsize=(9, 9))
        for i, ring in enumerate(polys):
            xs = [p[1] for p in ring] + [ring[0][1]]
            ys = [p[0] for p in ring] + [ring[0][0]]
            ax.fill(xs, ys, alpha=0.15, label=f"fence{i}" if len(polys) > 1 else "geoFence")
            ax.plot(xs, ys, linewidth=1.2)
        ax.plot(a_lon, a_lat, "k^", markersize=10, label="anchor")

        bd = geo.points_bd(pts)
        xs = [p[1] for p in bd]
        ys = [p[0] for p in bd]
        ax.scatter(xs, ys, c="red", s=60, zorder=5, label="打卡点")
        for i, (p, (la, lo)) in enumerate(zip(pts, bd)):
            name = p.get("pointName") or f"P{i}"
            must = "*" if p.get("isMustPoint") else ""
            ax.annotate(f"{name}{must}", (lo, la), fontsize=8,
                        xytext=(5, 5), textcoords="offset points")

        ax.set_xlabel("longitude (BD-09)")
        ax.set_ylabel("latitude (BD-09)")
        ax.set_title(f"geoFence + points  (runAreaId={run_area_id}, {len(pts)} pts)")
        ax.legend()
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(alpha=0.3)
        fig.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
        print(f"[plot] 已保存 {OUT_PNG}")
    finally:
        sess.logout()


if __name__ == "__main__":
    main()
