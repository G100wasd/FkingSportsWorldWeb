"""全局配置：backend/projectConfig.json 的单例加载/保存。

所有模块统一从 CFG 读取；save() 用于运行时写回（如按账号持久化锚点）。
改配置后需重启后端生效。
"""

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[2] / "projectConfig.json"


def load() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


CFG = load()


def save(cfg: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
