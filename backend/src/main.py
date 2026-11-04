"""Web 后端入口：登录 API + 托管前端页面。

运行（在 backend/src 目录下）:
    python main.py            # 或 python -m uvicorn main:app --port 8000
然后浏览器访问 http://127.0.0.1:8000
"""

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
    """BD-09 锚点坐标：服务端按它就近分配打卡点，并作为拟合参考原点。"""
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


@app.post("/api/runarea")
def api_runarea(body: AnchorBody):
    """以锚点确定跑步区域：拉围栏+实时打卡点 -> 体育场拟合 -> 轨迹生成。

    返回前端画图所需的全部 BD-09 数据（前端自行转 GCJ-02 上图）。
    """
    if _session is None:
        raise HTTPException(401, "请先登录")
    try:
        area = runarea.fetch_run_area(_session, body.lat, body.lon)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(502, f"拉取围栏/打卡点失败: {e}")
    try:
        dense, arcs, center, fit = stadium.make_stadium_ring(
            area["points_bd"], area["ref_center"], area["stadium_ring"])
        locs = track.build_track(
            2250.0, 720, None, area["points_bd"],
            fence_ring=area["stadium_ring"], ring=(dense, arcs, center))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(502, f"拟合/轨迹生成失败: {e}")
    return {
        "polygons": area["polygons"],
        "stadium_idx": area["stadium_idx"],
        "ref_center": area["ref_center"],
        "points": [{"name": p.get("pointName"), "isFixed": p.get("isFixed"),
                    "lat": p.get("lat"), "lon": p.get("lon")}
                   for p in area["points"]],
        "points_bd": area["points_bd"],
        "track_bd": fit["track_bd"],
        "locs": [[q["gLat"], q["gLng"]] for q in locs],
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
