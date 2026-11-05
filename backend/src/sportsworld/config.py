"""全局配置：backend/projectConfig.json 的单例加载/保存。

所有模块统一从 CFG 读取；save() 用于运行时写回（如按账号持久化锚点）。
改配置后需重启后端生效。
"""

import json
import shutil
import sys
from pathlib import Path


def _config_path() -> Path:
    """冻结(exe)时用 exe 旁的 projectConfig.json（可写，随程序分发）；
    缺失则从打包内置的默认配置播种一份出来。"""
    if getattr(sys, "frozen", False):
        p = Path(sys.executable).resolve().parent / "projectConfig.json"
        if not p.exists():
            bundled = Path(sys._MEIPASS) / "projectConfig.json"
            try:
                if bundled.exists():
                    shutil.copy2(bundled, p)
            except Exception:
                pass
        return p
    return Path(__file__).resolve().parents[2] / "projectConfig.json"


CONFIG_PATH = _config_path()


def load() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


CFG = load()


def save(cfg: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
