"""FastAPI 端点层：登录/配置/拉点/拟合/定时提交 + 托管前端页面。"""

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import CFG, load as cfg_load, save as cfg_save
from ..services.auth import login, stable_identity
from . import pipeline
from . import queue as submitq
from . import state
from .pipeline import RUN as _RUN

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
    0/未提供时回退到 defaultsConfig.json 的 run 段（配置卡"保存"的值）。"""
    dist: float = 0.0
    dur: int = 0
    start_ms: int = 0
    reseed: bool = False   # True=重掷随机实现（手动"拟合轨迹"/自动跑）；
                           # False=沿用上次种子（改配置后的重算）


class RunConfigBody(BaseModel):
    """配置卡"保存"提交体：只认字段级更新，未给字段保持原值。"""
    default_dist: float | None = None
    default_dur: int | None = None
    # 日期/伪装时间为前端会话值，不落盘——缺省=本地当前日期/时刻
    start_window: list | None = None
    alt_cap: float | None = None          # 生成的最大海拔（track 段）
    cad_max: float | None = None          # 生成的最大步频（track 段）
    stride_mean: float | None = None      # 生成步幅均值 m（track 段）


class DeferBody(BaseModel):
    """延迟提交：锚点 + 目标 + 未来的伪装起跑时刻（毫秒时间戳）。"""
    lat: float
    lon: float
    dist: float = 0.0
    dur: int = 0
    start_ms: int


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
    """持久化跑步配置到 defaultsConfig.json 的 run/track 段。"""
    cfg = cfg_load("defaults")
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
    cfg_save("defaults", cfg)
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
    info["auto"] = state.auto_for(uname) if sess else False
    if err:
        info["login_error"] = err
    return info


@app.post("/api/login")
def api_login(body: LoginBody):
    # 空账号/密码直接拒：否则 stable_identity("") 会在 accounts 里
    # 落一条 key 为空串的垃圾记录（指纹生成先于登录校验）
    u = body.username.strip()
    if not u or not body.password:
        raise HTTPException(400, "账号或密码为空")
    device = state.clean_device(body.device)
    try:
        sess = login(u, body.password, identity=stable_identity(u, device))
    except Exception as e:
        raise HTTPException(502, f"登录失败: {e}")
    state.reset_last()   # 换号/重登：上一个账号的点位与拟合一律作废
    state.save_account(u, body.password, device)
    with state.lock:
        key = (u, body.password)
        old = state.sess_cache["sess"] if state.sess_cache["key"] != key else None
        state.sess_cache["sess"], state.sess_cache["key"] = sess, key
    if old is not None:
        old.logout()  # 顶号前登出旧会话
    info = state.sess_info(sess)
    info["anchor"] = state.anchor_for(u)
    info["auto"] = state.auto_for(u)
    return info


@app.post("/api/logout")
def api_logout():
    state.reset_last()
    state.close_session()
    state.clear_account()
    return state.sess_info(None)


@app.post("/api/points")
def api_points(body: AnchorBody):
    """按锚点拉实时打卡点：后台自动完成 围栏 -> 计分跑申请 -> 取点。"""
    return pipeline.fetch_points(state.get_session(), body.lat, body.lon)


@app.post("/api/fit")
def api_fit(body: FitBody):
    """阶段二：对暂存的打卡点做体育场拟合 + 轨迹生成（纯本地，不触发限流）。"""
    return pipeline.run_fit(state.get_session(), body.dist, body.dur,
                            body.start_ms, body.reseed)


@app.post("/api/submit")
def api_submit():
    """把最近拟合挂入定时提交队列：服务端日志保持
    points@m -> submit@m+时长+延迟 的自洽时间线，不再是 m+几秒。"""
    if state.last["fit"] is None:
        raise HTTPException(409, "请先拟合轨迹")
    if state.last["fit"]["username"] != state.current_user():
        raise HTTPException(409, "拟合数据属于其他账号，请重新拟合")
    run_uuid = state.last["fit"]["run_uuid"]
    job = submitq.enqueue_submit(state.last["fit"])
    eta = max(0, (job["fire_at"] - submitq.now_ms()) // 1000)
    return {"scheduled": True, "uuid": run_uuid, "fire_at": job["fire_at"],
            "eta_s": int(eta), "already_queued": job["created_ms"] < submitq.now_ms()}


@app.post("/api/submit/deferred")
def api_submit_deferred(body: DeferBody):
    """延迟提交：伪装起跑时刻尚未到来时，不需要先拟合——
    排一个占位任务，到点后由后台自己 拉点 -> 拟合 -> 等跑完 -> 提交。"""
    user = state.current_user()
    if not user:
        raise HTTPException(401, "请先登录")
    now = submitq.now_ms()
    if body.start_ms <= now + 30_000:
        raise HTTPException(400, "起跑时刻不在未来，直接拟合后提交即可")
    if any(j.get("username") == user and j.get("kind") == "deferred"
           for j in submitq.pending().values()):
        raise HTTPException(409, "已有延迟提交任务在排队")
    dist = body.dist or _RUN.get("default_dist", 2200.0)
    dur = body.dur or _RUN.get("default_dur", 720)
    job = submitq.enqueue_deferred(user, body.lat, body.lon,
                                   dist, dur, body.start_ms)
    return {"scheduled": True, "deferred": True, "uuid": job["run_uuid"],
            "fire_at": job["fire_at"],
            "eta_s": max(0, int((job["fire_at"] - now) // 1000))}


@app.get("/api/submit/status")
def api_submit_status():
    """队列状态：pending 列表 + 最近一次执行结果（供倒计时/完成展示）。"""
    now = submitq.now_ms()
    pend = [{"run_uuid": u, "fire_at": j["fire_at"],
             "eta_s": max(0, int((j["fire_at"] - now) // 1000)),
             "state": j.get("state", "pending"),
             "kind": j.get("kind", "submit"),
             "stage": j.get("stage", ""),
             "from_deferred": j.get("from_deferred"),
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
    return {"cancelled": uuid}


@app.on_event("startup")
def _start_submit_worker():
    submitq.start_worker()


@app.on_event("shutdown")
def _on_shutdown():
    # 退出时只登出内存会话，不清 json 凭据——重启后自动重登
    state.close_session()
