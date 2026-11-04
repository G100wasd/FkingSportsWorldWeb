"""命令行账密输入：回车使用 projectConfig.json 中的默认凭据。"""

import getpass

from sportsworld.config import CFG

_acc = CFG.get("account") or {}
DEFAULT_USERNAME = _acc.get("username", "")
DEFAULT_PASSWORD = _acc.get("password", "")


def get_credentials() -> tuple[str, str]:
    username = input(f"账号 [{DEFAULT_USERNAME}]: ").strip() or DEFAULT_USERNAME
    password = getpass.getpass(f"密码 [{''.join('*' * len(DEFAULT_PASSWORD))}]: ").strip() or DEFAULT_PASSWORD
    return username, password
