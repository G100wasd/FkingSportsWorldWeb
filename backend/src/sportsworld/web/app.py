"""FastAPI 端点层：登录/配置/拉点/拟合/定时提交 + 托管前端页面。"""

import datetime as dt
import json
import sys
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import CFG, load as cfg_load, save as cfg_save
from ..domain import stadium, track, wire
from ..services import runarea, submit
from ..services.auth import login, stable_identity
from . import queue as submitq
from . import state

FRONTEND_DIR = (Path(sys._MEIPASS) / "frontend"
                if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parents[4] / "frontend")

app = FastAPI(title="wasdSports Web")


@app.middleware("http")
async def _no_cache_static(req, call_next):
    """静态资源禁缓存：前端 JS/CSS 改动无需手动清浏览器缓存。"""
    resp = await call_next(req)
    if req.url.path == "/" or req.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

_RUN = CFG.get("run") or {}


class LoginBody(BaseModel):
    username: str
    password: str
    device: dict | None = None   # 前端探测的设备环境 {platform,device_name,os_version}


class AnchorBody(BaseModel):
    """锚点坐标：前端传 GCJ-02 原值（与 _fence_test 同款锚点），
    服务端按该数值分配打卡点，并作为拟合参考原点。"""
    lat: float
    lon: float


class FitBody(BaseModel):
    """拟合目标：距离（米）+ 用时（秒）+ 伪装起跑时刻（毫秒时间戳）。
    0/未提供时回退到 projectConfig.json 的 run 段（配置卡"保存"的值）。"""
    dist: float = 0.0
    dur: int = 0
    start_ms: int = 0


class RunConfigBody(BaseModel):
    """配置卡"保存"提交体：只认字段级更新，未给字段保持原值。"""
    default_dist: float | None = None
    default_dur: int | None = None
    # 日期/伪装时间为前端会话值，不落盘——缺省=本地当前日期/时刻
    start_window: list | None = None
    alt_cap: float | None = None          # 生成的最大海拔（track 段）
    cad_max: float | None = None          # 生成的最大步频（track 段）
    stride_mean: float | None = None      # 生成步幅均值 m（track 段）


class CancelBody(BaseModel):
    run_uuid: str | None = None   # 不给则取消当前账号唯一排队任务


def _track_cfg() -> dict:
    """track 段下发表单字段的回显（api_defaults/api_config 共用）。"""
    t = CFG.get("track") or {}
    return {"alt_cap": t.get("alt_cap", 55.5),
            "cad_max": t.get("cad_max", 260),
            "stride_mean": t.get("stride_mean", 1.2)}


@app.get("/")
def index():
    # no-store：本地工具页面每次拿最新 HTML，避免旧缓存掩盖前端改动
    return FileResponse(FRONTEND_DIR / "index.html",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/defaults")
def api_defaults():
    """前端默认值：凭据预填 + run/track/frontend 配置下发（JSON 单一来源）。"""
    acc = state.account()
    return {
        "username": acc.get("username", ""),
        "password": acc.get("password", ""),
        "run": _RUN,
        "track": _track_cfg(),
        "frontend": CFG.get("frontend") or {},
    }


@app.post("/api/config")
def api_config(body: RunConfigBody):
    """持久化跑步配置到 projectConfig.json 的 run/track 段。"""
    cfg = cfg_load()
    run = cfg.setdefault("run", {})
    for k in ("default_dist", "default_dur", "start_window"):
        v = getattr(body, k, None)
        if v is not None and v != "":
            run[k] = v
    for k in ("alt_cap", "cad_max", "stride_mean"):
        v = getattr(body, k, None)
        if v is not None and v > 0:
            cfg.setdefault("track", {})[k] = v
            # CFG["track"] 与 trackgen._T 是同一字典对象：原地更新即运行期生效
            (CFG.setdefault("track", {}))[k] = v
    cfg_save(cfg)
    _RUN.update(run)   # FitBody 默认值在类定义时已绑定；同步字典让后续读取生效
    state.last["fit"] = None   # 配置已变：旧拟合的提交载荷作废
    return {"run": _RUN, "track": _track_cfg()}


@app.get("/api/session")
def api_session():
    # 凭据在则自动重登（登录持久化），失败视为未登录并透传原因
    err = None
    try:
        sess = state.get_session()
    except Exception as e:
        sess, err = None, str(e)
    uname = state.current_user() if sess else ""
    info = state.sess_info(sess)
    info["anchor"] = state.anchor_for(uname) if sess else None
    info["has_receipt"] = state.has_receipt(uname) if sess else False
    if err:
        info["login_error"] = err
    return info


@app.post("/api/login")
def api_login(body: LoginBody):
    u = body.username.strip()
    try:
        sess = login(u, body.password,
                     identity=stable_identity(u, body.device))
    except Exception as e:
        raise HTTPException(502, f"登录失败: {e}")
    state.save_account(u, body.password, body.device)
    with state.lock:
        key = (u, body.password)
        old = state.sess_cache["sess"] if state.sess_cache["key"] != key else None
        state.sess_cache["sess"], state.sess_cache["key"] = sess, key
    if old is not None:
        old.logout()  # 顶号前登出旧会话
    info = state.sess_info(sess)
    info["anchor"] = state.anchor_for(u)
    info["has_receipt"] = state.has_receipt(u)
    return info


@app.post("/api/logout")
def api_logout():
    state.last["fit"] = None
    state.close_session()
    state.clear_account()
    return state.sess_info(None)


@app.post("/api/points")
def api_points(body: AnchorBody):
    """按锚点拉实时打卡点：后台自动完成 围栏 -> 计分跑申请 -> 取点。"""
    sess = state.get_session()
    state.last["anchor"] = (body.lat, body.lon)
    state.last["fit"] = None          # 新点位使旧拟合作废，防止误提交过期数据
    if state.last["fences"] is None:
        try:
            state.last["fences"] = runarea.fetch_fences(sess)
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(502, f"拉取围栏失败: {e}")
    try:
        # 真实当前时间：假正午戳与请求日志时间矛盾是审计指纹
        state.last["points_ts"] = int(dt.datetime.now().timestamp() * 1000)
        state.last["area"] = runarea.fetch_area_points(
            sess, state.last["fences"], body.lat, body.lon,
            ts_ms=state.last["points_ts"])
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(502, f"拉取打卡点失败: {e}")
    area = state.last["area"]
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
    sess = state.get_session()
    if state.last["area"] is None:
        raise HTTPException(409, "请先获取打卡点")
    area = state.last["area"]
    dist = body.dist or _RUN.get("default_dist", 2200.0)
    dur = body.dur or _RUN.get("default_dur", 720)
    if not (100.0 <= dist <= 50000.0) or not (30 <= dur <= 14400):
        raise HTTPException(400, "目标距离/用时超出合理范围")
    # 起跑时刻：锚定拉点时刻（真实流程=拉到点后开跑）。
    # 定时队列已接管提交时机——记录结束在未来合法，调度器会
    # 等到 end+delay 再发，不再需要"保证已结束"的回拨钳制；
    # 未拉点/未传参才兜底回拨一个已完成窗口
    now_ms = int(dt.datetime.now().timestamp() * 1000)
    start_ms = body.start_ms or (
        state.last["points_ts"] if state.last["points_ts"]
        else now_ms - (dur + 60) * 1000)
    try:
        dense, arcs, center, fit = stadium.make_stadium_ring(
            area["points_bd"], area["ref_center"], area["stadium_ring"])
        trk = track.build_track(
            dist, dur, None, area["points_bd"],
            fence_ring=area["stadium_ring"], ring=(dense, arcs, center),
            start_ms=start_ms)
        locs = trk["locations"]
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(502, f"拟合/轨迹生成失败: {e}")
    if state.last["anchor"]:
        try:
            state.save_anchor(state.current_user(),
                              state.last["anchor"][0], state.last["anchor"][1])
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
        trk, uid=sess.uid,
        selected_unid=int(sess.unid) if str(sess.unid).isdigit() else 0,
        policy=scoring.get("policy") or 0,
        min_distance=int(scoring.get("minDistance") or 0),
        face_check=_RUN.get("face_check", 0),
        five_point_json=five_point,
        address=_RUN.get("address", ""))
    # 提交载荷整份暂存：/api/submit 必须原样重放同一 uuid/body（runef 依赖）；
    # track/five_point 供 OBS 对象复用（fixed_point_json 与提交体同源）
    state.last["fit"] = {"body": record_body, "run_uuid": run_uuid,
                         "policy_ts": scoring.get("policy_ts") or "",
                         "username": state.current_user(), "track": trk,
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
            "max_step_freq": trk.get("maxStepFreq"),
            "max_stride": trk.get("maxStride"),
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
        "submitted": bool(state.receipt_for(run_uuid)),
    }


@app.post("/api/submit")
def api_submit():
    """把最近拟合挂入定时提交队列：服务端日志保持
    points@m -> submit@m+时长+延迟 的自洽时间线，不再是 m+几秒。"""
    if state.last["fit"] is None:
        raise HTTPException(409, "请先拟合轨迹")
    if state.last["fit"]["username"] != state.current_user():
        raise HTTPException(409, "拟合数据属于其他账号，请重新拟合")
    run_uuid = state.last["fit"]["run_uuid"]
    old = state.receipt_for(run_uuid)
    if old:
        return {"rrid": old["rrid"], "uuid": run_uuid, "already": True,
                "obs_ok": old.get("obs_ok", 0), "obs_total": 2}
    job = submitq.enqueue_submit(state.last["fit"])
    eta = max(0, (job["fire_at"] - submitq.now_ms()) // 1000)
    return {"scheduled": True, "uuid": run_uuid, "fire_at": job["fire_at"],
            "eta_s": int(eta), "already_queued": job["created_ms"] < submitq.now_ms()}


@app.get("/api/submit/status")
def api_submit_status():
    """队列状态：pending 列表 + 最近一次执行结果（供倒计时/完成展示）。"""
    now = submitq.now_ms()
    pend = [{"run_uuid": u, "fire_at": j["fire_at"],
             "eta_s": max(0, int((j["fire_at"] - now) // 1000)),
             "state": j.get("state", "pending"),
             "attempts": j.get("attempts", 0),
             "error": j.get("last_error")}
            for u, j in submitq.pending().items()
            if j.get("username") == state.current_user()]
    out = {"pending": pend, "firing": [u for u in submitq.firing],
           "now_ms": now}
    if submitq.last_fired:
        out["last"] = submitq.last_fired
    return out


@app.post("/api/submit/cancel")
def api_submit_cancel(body: CancelBody):
    jobs = submitq.pending()
    uuid = body.run_uuid
    if not uuid:
        mine = [u for u, j in jobs.items()
                if j.get("username") == state.current_user()]
        if len(mine) != 1:
            raise HTTPException(409, f"排队任务数={len(mine)}，请指定 run_uuid")
        uuid = mine[0]
    if uuid not in jobs:
        raise HTTPException(404, "无此排队任务")
    if uuid in submitq.firing:
        raise HTTPException(409, "任务正在执行中，无法取消")
    jobs.pop(uuid)
    submitq.save_pending(jobs)
    return {"cancelled": uuid}


@app.on_event("startup")
def _start_submit_worker():
    submitq.start_worker()


@app.on_event("shutdown")
def _on_shutdown():
    # 退出时只登出内存会话，不清 json 凭据——重启后自动重登
    state.close_session()
