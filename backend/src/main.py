"""Web 后端入口：登录 API + 托管前端页面。

运行（在 backend/src 目录下）:
    python main.py            # 或 python -m uvicorn main:app --port 8000
然后浏览器访问 http://127.0.0.1:8000
"""

import datetime as dt
import threading
import traceback
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from prompts import DEFAULT_PASSWORD, DEFAULT_USERNAME
from sportsworld.domain import stadium, track
from sportsworld.services import runarea
from sportsworld.services.auth import login

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="NekoSportsWorld Web")

# 单用户本地应用：只保留一个登录会话；锁保护登录/登出竞态
_lock = threading.Lock()
_session = None


class LoginBody(BaseModel):
    username: str
    password: str


class AnchorBody(BaseModel):
    """锚点坐标：前端传 GCJ-02 原值（与 _fence_test 同款锚点），
    服务端按该数值分配打卡点，并作为拟合参考原点。"""
    lat: float
    lon: float


def _sess_info(sess):
    return {
        "logged_in": sess is not None,
        "uid": sess.uid if sess else 0,
        "name": sess.name if sess else "",
        "unid": sess.unid if sess else "",
    }


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/defaults")
def api_defaults():
    """前端输入框默认值（凭据只存在 prompts.py 一处）。"""
    return {"username": DEFAULT_USERNAME, "password": DEFAULT_PASSWORD}


@app.get("/api/session")
def api_session():
    return _sess_info(_session)


@app.post("/api/login")
def api_login(body: LoginBody):
    global _session
    try:
        sess = login(body.username.strip(), body.password)
    except Exception as e:
        raise HTTPException(502, f"登录失败: {e}")
    with _lock:
        old, _session = _session, sess
    if old is not None:
        old.logout()  # 顶号前登出旧会话
    return _sess_info(sess)


@app.post("/api/logout")
def api_logout():
    global _session
    with _lock:
        sess, _session = _session, None
    if sess is not None:
        sess.logout()
    return _sess_info(None)


_last_fences = None  # 最近一次拉取的围栏数据（限流外，复用于拉点）
_last_area = None    # 最近一次拉取的跑步区域（供 /api/fit 复用，避免重复拉点）


@app.post("/api/points")
def api_points(body: AnchorBody):
    """按锚点拉实时打卡点：后台自动完成 围栏 -> 计分跑申请 -> 取点。"""
    global _last_fences, _last_area
    if _session is None:
        raise HTTPException(401, "请先登录")
    if _last_fences is None:
        try:
            _last_fences = runarea.fetch_fences(_session)
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(502, f"拉取围栏失败: {e}")
    try:
        # 与 _fence_test 一致：假 12:00 时间戳 + 顶层 runAreaId
        fake_ms = int(dt.datetime.combine(
            dt.date.today(), dt.time(12, 0)).timestamp() * 1000)
        _last_area = runarea.fetch_area_points(
            _session, _last_fences, body.lat, body.lon, ts_ms=fake_ms)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(502, f"拉取打卡点失败: {e}")
    area = _last_area
    sel = next((a for a in area["areas"] if a["idx"] == area["area_idx"]), None)
    hit = next((a for a in area["areas"] if a["idx"] == area["stadium_idx"]), None)
    return {
        "area_name": sel["name"] if sel else "(默认区域)",
        "stadium_name": hit["name"] if hit else "",
        "run_area_id": area["area_run_area_id"],
        "points": [{"name": p.get("pointName"), "isFixed": p.get("isFixed"),
                    "lat": p.get("lat"), "lon": p.get("lon")}
                   for p in area["points"]],
        "scoring": area["scoring"],
    }


@app.post("/api/fit")
def api_fit():
    """阶段二：对暂存的打卡点做体育场拟合 + 轨迹生成（纯本地，不触发限流）。"""
    if _session is None:
        raise HTTPException(401, "请先登录")
    if _last_area is None:
        raise HTTPException(409, "请先获取打卡点")
    area = _last_area
    try:
        dense, arcs, center, fit = stadium.make_stadium_ring(
            area["points_bd"], area["ref_center"], area["stadium_ring"])
        locs = track.build_track(
            2250.0, 720, None, area["points_bd"],
            fence_ring=area["stadium_ring"], ring=(dense, arcs, center))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(502, f"拟合/轨迹生成失败: {e}")
    perim = fit["perimeter"] or 1.0
    total_dis = float(locs[-1]["totalDis"]) if locs else 0.0
    total_time = int(locs[-1]["totalTime"]) if locs else 0
    return {
        "track_bd": fit["track_bd"],
        "locs": [[q["gLat"], q["gLng"], int(q["totalDis"] // perim)]
                 for q in locs],
        "locs_full": locs,
        "snap_idx": [i for i, q in enumerate(locs) if q.get("snapped")],
        "summary": {
            "distance_m": round(total_dis, 1),
            "duration_s": total_time,
            "pace_s_per_km": round(total_time / (total_dis / 1000.0), 1)
                             if total_dis > 0 else None,
            "avg_speed_mps": round(total_dis / total_time, 3)
                             if total_time > 0 else None,
            "point_count": len(locs),
            "lap_count": int(total_dis // perim) + 1 if total_dis > 0 else 0,
            "snapped": sum(1 for q in locs if q.get("snapped")),
        },
        "fit": {
            "cx_bd": fit["cx_bd"], "angle_deg": fit["angle"] * 180 / 3.141592653589793,
            "straight": fit["straight"], "radius": fit["radius"],
            "perimeter": fit["perimeter"], "point_errors": fit["point_errors"],
            "mean_err": fit["mean_err"], "max_err": fit["max_err"],
            "fence_margin": fit["fence_margin"], "converged": fit["converged"],
            "alt_solutions": fit["alt_solutions"],
        },
    }


@app.on_event("shutdown")
def _on_shutdown():
    # 后端进程退出时自动登出，避免账号残留登录态触发 10121
    api_logout()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
