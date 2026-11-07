"""Web 层共享状态：登录会话缓存、账号/锚点/回执持久化、拉点/拟合暂存。"""

import datetime as dt
import threading

from fastapi import HTTPException

from ..config import load as cfg_load, save as cfg_save
from ..services.auth import login, stable_identity

# 登录态唯一事实源 = projectConfig.json 的 account；
# sess_cache 只是进程内缓存，避免每个请求都重登（GT4 验证+限流）
lock = threading.Lock()
sess_cache = {"sess": None, "key": None}

# 最近一次的拉点/拟合暂存（限流外复用；新拉点/改配置/登出时作废拟合）
last = {"fences": None,   # 最近一次拉取的围栏数据（复用于拉点）
        "area": None,     # 最近一次拉取的跑步区域（供 /api/fit 复用）
        "anchor": None,   # 最近一次取点的锚点 (lat, lon) = GCJ-02 原值
        "fit": None,      # 最近一次拟合的提交载荷 {body, run_uuid, ...}
        "points_ts": 0}   # 最近一次拉点时刻（ms）：记录起点锚定它


def account() -> dict:
    return (cfg_load().get("account") or {})


def current_user() -> str:
    return account().get("username") or ""


def save_account(username: str, password: str, device):
    cfg = cfg_load()
    cfg["account"] = {"username": username, "password": password,
                      "device": device or {}}
    cfg_save(cfg)


def clear_account():
    cfg = cfg_load()
    cfg["account"] = {"username": "", "password": ""}
    cfg_save(cfg)


def get_session():
    """从 json 凭据取会话：凭据不变复用已登录会话，否则重新登录。"""
    acc = account()
    u, p = acc.get("username") or "", acc.get("password") or ""
    if not u or not p:
        raise HTTPException(401, "请先登录")
    key = (u, p)
    with lock:
        sess = sess_cache["sess"]
        if sess is not None and sess_cache["key"] == key:
            return sess
    sess = login(u, p, identity=stable_identity(u, acc.get("device")))
    with lock:
        old = sess_cache["sess"] if sess_cache["key"] != key else None
        sess_cache["sess"], sess_cache["key"] = sess, key
    if old is not None:
        old.logout()   # 凭据已变更：登出旧账号的会话
    return sess


def close_session():
    """退出/登出时释放内存会话（不动 json 凭据）。"""
    with lock:
        sess = sess_cache["sess"]
        sess_cache["sess"], sess_cache["key"] = None, None
    if sess is not None:
        sess.logout()


def sess_info(sess):
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


def anchor_for(username: str):
    """该账号上次拟合使用的锚点（GCJ-02 原值），无记录返回 None。"""
    return (cfg_load().get("anchors") or {}).get(username)


def save_anchor(username: str, lat: float, lon: float):
    if not username:
        return
    cfg = cfg_load()
    cfg.setdefault("anchors", {})[username] = {"lat": lat, "lon": lon}
    cfg_save(cfg)


def receipt_for(run_uuid: str):
    """本地提交回执（WAL）：uuid -> {rrid, ts, username}。防重复提交。"""
    return (cfg_load().get("receipts") or {}).get(run_uuid)


def has_receipt(username: str) -> bool:
    """该账号是否有成功提交回执（前端"自动跑"按钮的解锁条件之一）。"""
    if not username:
        return False
    return any(r.get("username") == username
               for r in (cfg_load().get("receipts") or {}).values())


def save_receipt(run_uuid: str, rrid: int, obs_ok: int = 0):
    cfg = cfg_load()
    cfg.setdefault("receipts", {})[run_uuid] = {
        "rrid": rrid, "username": current_user(), "obs_ok": obs_ok,
        "ts": int(dt.datetime.now().timestamp() * 1000)}
    cfg_save(cfg)
