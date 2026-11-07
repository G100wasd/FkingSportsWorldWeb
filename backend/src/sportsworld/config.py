"""配置按生命周期拆成四份 JSON，各自独立加载/保存。

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

import json
import shutil
import sys
from pathlib import Path

STORES = ("server", "defaults", "session", "accounts")


def _dir() -> Path:
    """冻结(exe)时用 exe 旁目录（可写，随程序分发），否则用 backend/。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _path(name: str) -> Path:
    p = _dir() / f"{name}Config.json"
    if getattr(sys, "frozen", False) and not p.exists():
        bundled = Path(sys._MEIPASS) / p.name   # 缺失则从打包内置副本播种
        try:
            if bundled.exists():
                shutil.copy2(bundled, p)
        except Exception:
            pass
    return p


def load(name: str) -> dict:
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(name: str, data: dict) -> None:
    _path(name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


CFG = {**load("server"), **load("defaults")}


# ---------- 按账号持久化记录（CF 上即单个 DO 的 storage） ----------

def account_record(username: str) -> dict:
    """{identity, anchor, auto}；无记录返回 {}。"""
    if not username:
        return {}
    return (load("accounts").get("accounts") or {}).get(username) or {}


def update_account_record(username: str, **fields) -> dict:
    """字段级更新并落盘；未给的字段保持原值。"""
    data = load("accounts")
    rec = data.setdefault("accounts", {}).setdefault(username, {})
    rec.update(fields)
    save("accounts", data)
    return rec
