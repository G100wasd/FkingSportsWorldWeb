"""命令行登录验证入口。"""

from prompts import get_credentials
from sportsworld.services.auth import login

if __name__ == "__main__":
    username, password = get_credentials()
    try:
        login(username, password)
    except Exception as e:
        print(f"[login] 失败: {e}")
        raise
