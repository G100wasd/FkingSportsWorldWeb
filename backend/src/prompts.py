"""命令行账密输入：回车使用已保存的默认凭据。"""

import getpass

DEFAULT_USERNAME = "18818920029"
DEFAULT_PASSWORD = "Wasd10086"


def get_credentials() -> tuple[str, str]:
    username = input(f"账号 [{DEFAULT_USERNAME}]: ").strip() or DEFAULT_USERNAME
    password = getpass.getpass(f"密码 [{''.join('*' * len(DEFAULT_PASSWORD))}]: ").strip() or DEFAULT_PASSWORD
    return username, password
