"""Web 层共享状态：登录会话缓存、账号/锚点持久化、拉点/拟合暂存。"""

import threading

from fastapi import HTTPException

from ..config import (account_record, load as cfg_load, save as cfg_save,
                      update_account_record)
from ..services.auth import login, stable_identity

# 登录态唯一事实源 = sessionConfig.json 的 account；
# sess_cache 只是进程内缓存，避免每个请求都重登（GT4 验证+限流）
lock = threading.Lock()
sess_cache = {"sess": None, "key": None}

# 最近一次的拉点/拟合暂存（限流外复用；新拉点/改配置/登出时作废拟合）
last = {"fences": None,   # 最近一次拉取的围栏数据（复用于拉点）
        "area": None,     # 最近一次拉取的跑步区域（供 /api/fit 复用）
        "anchor": None,   # 最近一次取点的锚点 (lat, lon) = GCJ-02 原值
        "ring": None,     # 跑道几何拟合缓存 (dense, arcs, center, fit)：
                          # 只依赖点位/围栏，与跑步配置无关 → 改配置时复用
        "seed": None,     # 轨迹随机种子：改配置重算时复用，保证 GPS
                          # 抖动/漂移/摆动那层实现不变（只有配置影响结果）
        "fit": None,      # 最近一次拟合的提交载荷 {body, run_uuid, ...}
        "points_ts": 0}   # 最近一次拉点时刻（ms）：记录起点锚定它


def reset_last():
    """清空拉点/拟合暂存：登录、登出、切号时必须调用——
    否则新账号会看到并可能提交上一个账号的点位/轨迹。"""
    last.update({"fences": None, "area": None, "anchor": None,
                 "ring": None, "seed": None, "fit": None, "points_ts": 0})


def account() -> dict:
    """本机"当前登录者"（session 存储）。CF 上该概念不存在：
    身份来自每个请求的签名 cookie，而不是服务端全局状态。"""
    return (cfg_load("session").get("account") or {})


def current_user() -> str:
    return account().get("username") or ""


DEVICE_KEYS = ("platform", "device_name", "os_version")


def clean_device(device) -> dict:
    """设备环境字段白名单：只取三个已知键并截断长度。
    前端探测结果是不可信输入，原样落盘等于让客户端写任意 JSON。"""
    if not isinstance(device, dict):
        return {}
    out = {}
    for k in DEVICE_KEYS:
        v = device.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:64]
    return out


def save_account(username: str, password: str, device):
    cfg_save("session", {"account": {
        "username": username, "password": password,
        "device": clean_device(device)}})


def clear_account():
    cfg_save("session", {"account": {"username": "", "password": ""}})


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
    return account_record(username).get("anchor")


def save_anchor(username: str, lat: float, lon: float):
    """锚点只在真的变了（地图重新选点/定位）时落盘：
    run_fit 每次都会调用这里，无条件写会让每次拟合都整文件重写。"""
    if not username:
        return
    old = account_record(username).get("anchor") or {}
    if (abs(float(old.get("lat", 1e9)) - lat) < 1e-9 and
            abs(float(old.get("lon", 1e9)) - lon) < 1e-9):
        return
    update_account_record(username, anchor={"lat": lat, "lon": lon})


def auto_for(username: str) -> bool:
    """自动跑开关：该账号首次成功提交后置 true（此后按钮解锁）。"""
    return bool(account_record(username).get("auto"))


def set_auto(username: str, value: bool = True):
    rec = account_record(username)
    if not rec or bool(rec.get("auto")) == value:
        return                      # 无账号记录或已是目标值：不写盘
    update_account_record(username, auto=value)
