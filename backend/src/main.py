"""Web 后端入口：登录 API + 托管前端页面。

运行（在 backend/src 目录下）:
    python main.py            # 或 python -m uvicorn main:app --port 8000
然后浏览器访问 http://127.0.0.1:8000
"""

import datetime as dt
import json
import threading
import time
import traceback
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sportsworld.config import CFG, load as cfg_load, save as cfg_save
from sportsworld.domain import stadium, track, wire
from sportsworld.services import obs, records, runarea, submit
from sportsworld.services.auth import default_identity, login

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="NekoSportsWorld Web")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

# 单用户本地应用：只保留一个登录会话；锁保护登录/登出竞态
_lock = threading.Lock()
_session = None
_username = ""          # 当前会话登录名（锚点按账号区分）


def _anchor_for(username: str):
    """该账号上次拟合使用的锚点（GCJ-02 原值），无记录返回 None。"""
    return (cfg_load().get("anchors") or {}).get(username)


def _save_anchor(username: str, lat: float, lon: float):
    if not username:
        return
    cfg = cfg_load()
    cfg.setdefault("anchors", {})[username] = {"lat": lat, "lon": lon}
    cfg_save(cfg)


def _receipt_for(run_uuid: str):
    """本地提交回执（WAL）：uuid -> {rrid, ts, username}。防重复提交。"""
    return (cfg_load().get("receipts") or {}).get(run_uuid)


def _has_receipt(username: str) -> bool:
    """该账号是否有成功提交回执（前端"自动跑"按钮的解锁条件之一）。"""
    if not username:
        return False
    return any(r.get("username") == username
               for r in (cfg_load().get("receipts") or {}).values())


def _save_receipt(run_uuid: str, rrid: int, obs_ok: int = 0):
    cfg = cfg_load()
    cfg.setdefault("receipts", {})[run_uuid] = {
        "rrid": rrid, "username": _username, "obs_ok": obs_ok,
        "ts": int(dt.datetime.now().timestamp() * 1000)}
    cfg_save(cfg)


class LoginBody(BaseModel):
    username: str
    password: str
    device: dict | None = None   # 前端探测的设备环境 {platform,device_name,os_version}


class AnchorBody(BaseModel):
    """锚点坐标：前端传 GCJ-02 原值（与 _fence_test 同款锚点），
    服务端按该数值分配打卡点，并作为拟合参考原点。"""
    lat: float
    lon: float


_RUN = CFG.get("run") or {}


class FitBody(BaseModel):
    """拟合目标：距离（米）+ 用时（秒）+ 伪装起跑时刻（毫秒时间戳）。"""
    dist: float = _RUN.get("default_dist", 2200.0)
    dur: int = _RUN.get("default_dur", 720)
    start_ms: int = 0   # 0 = 未提供


def _sess_info(sess):
    iden = (sess.identity if sess else {}) or {}
    return {
        "logged_in": sess is not None,
        "uid": sess.uid if sess else 0,
        "name": sess.name if sess else "",
        "unid": sess.unid if sess else "",
        "platform": iden.get("platform", ""),
        "device_name": iden.get("device_name", ""),
        "os_version": iden.get("os_version", ""),
    }


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/defaults")
def api_defaults():
    """前端默认值：凭据预填 + run/frontend 两组配置下发（JSON 单一来源）。"""
    acc = CFG.get("account") or {}
    return {
        "username": acc.get("username", ""),
        "password": acc.get("password", ""),
        "run": _RUN,
        "frontend": CFG.get("frontend") or {},
    }


@app.get("/api/session")
def api_session():
    info = _sess_info(_session)
    info["anchor"] = _anchor_for(_username) if _session else None
    info["has_receipt"] = _has_receipt(_username) if _session else False
    return info


@app.post("/api/login")
def api_login(body: LoginBody):
    global _session, _username
    try:
        sess = login(body.username.strip(), body.password,
                     identity=default_identity(body.device))
    except Exception as e:
        raise HTTPException(502, f"登录失败: {e}")
    with _lock:
        old, _session = _session, sess
        _username = body.username.strip()
    if old is not None:
        old.logout()  # 顶号前登出旧会话
    info = _sess_info(sess)
    info["anchor"] = _anchor_for(_username)
    info["has_receipt"] = _has_receipt(_username)
    return info


@app.post("/api/logout")
def api_logout():
    global _session, _username, _last_fit
    with _lock:
        sess, _session = _session, None
        _username = ""
        _last_fit = None
    if sess is not None:
        sess.logout()
    return _sess_info(None)


_last_fences = None  # 最近一次拉取的围栏数据（限流外，复用于拉点）
_last_area = None    # 最近一次拉取的跑步区域（供 /api/fit 复用，避免重复拉点）
_last_anchor = None  # 最近一次取点的锚点 (lat, lon) = 前端提交的 GCJ-02 原值
_last_fit = None     # 最近一次拟合的提交载荷 {body, run_uuid, policy_ts, username}


@app.post("/api/points")
def api_points(body: AnchorBody):
    """按锚点拉实时打卡点：后台自动完成 围栏 -> 计分跑申请 -> 取点。"""
    global _last_fences, _last_area, _last_anchor, _last_fit
    if _session is None:
        raise HTTPException(401, "请先登录")
    _last_anchor = (body.lat, body.lon)
    _last_fit = None          # 新点位使旧拟合作废，防止误提交过期数据
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
def api_fit(body: FitBody):
    """阶段二：对暂存的打卡点做体育场拟合 + 轨迹生成（纯本地，不触发限流）。"""
    global _last_fit
    if _session is None:
        raise HTTPException(401, "请先登录")
    if _last_area is None:
        raise HTTPException(409, "请先获取打卡点")
    if not (100.0 <= body.dist <= 50000.0) or not (30 <= body.dur <= 14400):
        raise HTTPException(400, "目标距离/用时超出合理范围")
    area = _last_area
    start_ms = body.start_ms or int(dt.datetime.now().timestamp() * 1000)
    try:
        dense, arcs, center, fit = stadium.make_stadium_ring(
            area["points_bd"], area["ref_center"], area["stadium_ring"])
        trk = track.build_track(
            body.dist, body.dur, None, area["points_bd"],
            fence_ring=area["stadium_ring"], ring=(dense, arcs, center),
            start_ms=start_ms)
        locs = trk["locations"]
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(502, f"拟合/轨迹生成失败: {e}")
    if _last_anchor:
        try:
            _save_anchor(_username, _last_anchor[0], _last_anchor[1])
        except Exception:
            traceback.print_exc()   # 锚点持久化失败不影响拟合结果
    perim = fit["perimeter"] or 1.0
    total_dis = float(locs[-1]["totalDis"]) if locs else 0.0
    total_time = int(locs[-1]["totalTime"]) if locs else 0
    speed_win, step_win = wire.submit_windows(trk)
    five_point = wire.five_point_wrapper(
        area["points"], trk,
        run_area_id=area.get("meta_run_area_id", -1),
        geo_fences=area.get("geo_fences") or [],
        freedom_show_fence=area.get("freedom_show_fence", False))
    scoring = area.get("scoring") or {}
    record_body, run_uuid = submit.build_record_body(
        trk, uid=_session.uid,
        selected_unid=int(_session.unid) if str(_session.unid).isdigit() else 0,
        policy=scoring.get("policy") or 0,
        min_distance=int(scoring.get("minDistance") or 0),
        face_check=_RUN.get("face_check", 0),
        five_point_json=five_point,
        address=_RUN.get("address", ""))
    # 提交载荷整份暂存：/api/submit 必须原样重放同一 uuid/body（runef 依赖）；
    # track/five_point 供 OBS 对象复用（fixed_point_json 与提交体同源）
    _last_fit = {"body": record_body, "run_uuid": run_uuid,
                 "policy_ts": scoring.get("policy_ts") or "",
                 "username": _username, "track": trk,
                 "five_point": five_point}
    return {
        "track_bd": fit["track_bd"],
        "locs": [[q["gLat"], q["gLng"], int(q["totalDis"] // perim)]
                 for q in locs],
        "locs_full": locs,
        "snap_idx": [i for i, q in enumerate(locs) if q.get("snapped")],
        "speedPerTenSec": trk["speedPerTenSec"],
        "stepsPerTenSec": trk["stepsPerTenSec"],
        "segments": trk["segments"],
        "speed_windows": speed_win,
        "step_windows": step_win,
        "laps": wire.build_laps(trk, trk["startTime"]),
        "five_point": json.loads(five_point),
        "record_body": record_body,
        "run_uuid": run_uuid,
        "summary": {
            "distance_m": round(total_dis, 1),
            "duration_s": total_time,
            "start_ms": trk["startTime"] or None,
            "pace_s_per_km": round(total_time / (total_dis / 1000.0), 1)
                             if total_dis > 0 else None,
            "avg_speed_mps": round(total_dis / total_time, 3)
                             if total_time > 0 else None,
            "point_count": len(locs),
            "lap_count": int(total_dis // perim) + 1 if total_dis > 0 else 0,
            "snapped": sum(1 for q in locs if q.get("snapped")),
            "total_steps": trk["totalSteps"],
            "step_freq": trk["avgStepFreq"],
            "calorie": trk["calorie"],
            "avg_power": trk["avgPower"],
            "ascent_m": trk["totalAscent"],
            "valid_dis_m": round(trk["validDistance"], 1),
            "valid_time_s": trk["validTime"],
            "pace_milli": trk["paceMilli"],
        },
        "fit": {
            "cx_bd": fit["cx_bd"], "angle_deg": fit["angle"] * 180 / 3.141592653589793,
            "straight": fit["straight"], "radius": fit["radius"],
            "perimeter": fit["perimeter"], "point_errors": fit["point_errors"],
            "mean_err": fit["mean_err"], "max_err": fit["max_err"],
            "fence_margin": fit["fence_margin"], "converged": fit["converged"],
            "alt_solutions": fit["alt_solutions"],
        },
        "submitted": bool(_receipt_for(run_uuid)),
    }


@app.post("/api/submit")
def api_submit():
    """提交最近一次拟合的记录：save/record 双层信封 POST -> rrid -> 本地回执。"""
    if _session is None:
        raise HTTPException(401, "请先登录")
    if _last_fit is None:
        raise HTTPException(409, "请先拟合轨迹")
    if _last_fit["username"] != _username:
        raise HTTPException(409, "拟合数据属于其他账号，请重新拟合")
    run_uuid = _last_fit["run_uuid"]
    old = _receipt_for(run_uuid)
    if old:
        return {"rrid": old["rrid"], "uuid": run_uuid, "already": True,
                "obs_ok": old.get("obs_ok", 0), "obs_total": 2}
    try:
        result = submit.submit_record(
            _session, _last_fit["body"], run_uuid,
            _last_fit["policy_ts"])
    except submit.SubmitRejected as e:
        raise HTTPException(502, str(e))
    except submit.SubmitError as e:
        raise HTTPException(502, f"{e}（结果未知，本地已记日志，请勿盲目重试）")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(502, f"提交失败: {e}")
    rrid = result["rrid"]

    # OBS 上传（双 key）：失败只降级不影响已落库的记录
    obs_ok, obs_keys = 0, []
    try:
        time.sleep(1)
        obj = wire.build_obs_object(
            _last_fit["track"], rrid, run_uuid, _session.uid,
            _last_fit["five_point"])
        obs_keys = wire.obs_keys(_last_fit["track"], rrid, run_uuid)
        payload = json.dumps(obj, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
        obs_ok = obs.upload_both_keys(_session, obs_keys, payload)
    except Exception:
        traceback.print_exc()
    _save_receipt(run_uuid, rrid, obs_ok)

    # 详情验证：能回读到该 rrid 即记 verified
    verified = False
    try:
        time.sleep(2)
        verified = bool(records.fetch_one_record(_session, rrid))
    except Exception:
        traceback.print_exc()

    return {"rrid": rrid, "uuid": run_uuid, "already": False,
            "complete": result.get("complete"),
            "obs_ok": obs_ok, "obs_total": len(obs_keys),
            "verified": verified}


@app.on_event("shutdown")
def _on_shutdown():
    # 后端进程退出时自动登出，避免账号残留登录态触发 10121
    api_logout()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1",
            port=(CFG.get("server") or {}).get("listen_port", 8000))
