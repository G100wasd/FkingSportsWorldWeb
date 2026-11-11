"""配置按生命周期拆成四份 JSON，各自独立加载/保存。

真实配置文件（serverConfig/defaultsConfig/sessionConfig/accountsConfig.json）
是运行态产物，不入库；每份对应一个入库模板 {name}Config.example.json，
真实文件缺失或损坏时 load() 回退到模板，save() 才把真实文件写出。

| 文件 | 内容 | 生命周期 | CF 部署形态 |
|---|---|---|---|
| serverConfig.json   | server          | 部署常量，只读     | Worker 环境变量 / Secrets |
| defaultsConfig.json | run/track/frontend | 全局默认，偶尔改 | 镜像内只读默认 + 账号覆盖 |
| sessionConfig.json  | account         | "本机当前登录者"   | 不存在：身份由签名 cookie 携带 |
| accountsConfig.json | accounts[用户]  | 按账号持久化       | 每账号一个 Durable Object 的 storage |

CFG = server+defaults 的合并视图，进程启动时一次加载（trackgen 各模块
直接持有 CFG["track"] 这个字典对象，原地更新即运行期生效）。
session/accounts 是运行态：必须每次 load() 现读、改完立即 save()。
"""

import datetime as dt
import json
import os
import shutil
import sys
import threading
from pathlib import Path

STORES = ("server", "defaults", "session", "accounts")

# 所有 load/save 串行化：HTTP 线程池 + 队列 worker 会真并发，
# 读-改-写（update_account_record）若交错会静默丢字段
_io_lock = threading.RLock()


def _dir() -> Path:
    """冻结(exe)时用 exe 旁目录（可写，随程序分发），否则用 backend/。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _path(name: str) -> Path:
    return _dir() / f"{name}Config.json"


def _example_section(name: str) -> dict:
    """{name}Config.example.json 是入库的初始模板：真实配置文件缺失/损坏
    时兜底。exe 旁允许用户自带 example 覆盖内置副本。"""
    p = _dir() / f"{name}Config.example.json"
    if not p.exists() and getattr(sys, "frozen", False):
        p = Path(sys._MEIPASS) / p.name
    try:
        sec = json.loads(p.read_text(encoding="utf-8"))
        return dict(sec) if isinstance(sec, dict) else {}
    except Exception:
        return {}


def load(name: str) -> dict:
    """读配置。文件不存在 → example.json 对应节；**内容损坏 → 先备份再兜底**。

    损坏必须留痕：静默当成空配置，紧接着的一次 save 就会把残骸
    覆盖掉——设备指纹全丢、下次登录生成新 device_id，正是风控
    最敏感的信号。备份文件名带时间戳，可人工取回。
    """
    with _io_lock:
        p = _path(name)
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception:
            return _example_section(name)   # 不存在/读不了都走模板
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            bak = p.with_name(f"{p.stem}.corrupt-{stamp}.json")
            try:
                shutil.copy2(p, bak)
            except Exception:
                pass
            print(f"[config] !! {p.name} 内容损坏({e})，已备份为 {bak.name}；"
                  f"本次回退 example.json 模板，请人工核对后恢复")
            return _example_section(name)


def save(name: str, data: dict) -> None:
    """原子落盘：先写临时文件再同盘 rename。

    直接 write_text 是"先截断后写入"，进程在这期间被杀（关窗/崩溃）
    就会留下半截 JSON。
    """
    with _io_lock:
        p = _path(name)
        tmp = p.with_name(p.name + ".tmp")
        text = json.dumps(data, ensure_ascii=False, indent=2)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())   # 断电/强杀也不会只落一半
        tmp.replace(p)             # 同盘 rename：原子替换


CFG = {**load("server"), **load("defaults")}


# ---------- 按账号持久化记录（CF 上即单个 DO 的 storage） ----------

def account_record(username: str) -> dict:
    """{identity, anchor, auto}；无记录返回 {}。"""
    if not username:
        return {}
    return (load("accounts").get("accounts") or {}).get(username) or {}


def update_account_record(username: str, **fields) -> dict:
    """字段级更新并落盘；未给的字段保持原值。
    整个读-改-写持锁，避免并发线程互相覆盖。"""
    with _io_lock:
        data = load("accounts")
        rec = data.setdefault("accounts", {}).setdefault(username, {})
        rec.update(fields)
        save("accounts", data)
        return rec
