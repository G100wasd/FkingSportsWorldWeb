"""命令行登录验证入口。登录成功后注册 atexit，进程退出时自动登出。"""

import atexit

from prompts import get_credentials
from sportsworld.services.auth import login

if __name__ == "__main__":
    username, password = get_credentials()
    try:
        sess = login(username, password)
        atexit.register(sess.logout)
        input("登录成功，按回车退出（退出时自动登出）...")
    except Exception as e:
        print(f"[login] 失败: {e}")
        raise
