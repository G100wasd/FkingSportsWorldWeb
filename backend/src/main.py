"""Web 后端入口：登录 API + 托管前端页面。

运行（在 backend/src 目录下）:
    python main.py            # 或 python -m uvicorn main:app --port 8000
然后浏览器访问 http://127.0.0.1:8000

端点实现见 sportsworld/web/app.py，共享状态/定时队列见同包
state.py/queue.py；本文件只负责进程拉起（uvicorn / pywebview 外壳）。
"""

import os
import sys
import threading
import time
import traceback

import uvicorn

from sportsworld.config import CFG
from sportsworld.web import state
from sportsworld.web.app import app  # noqa: F401  # uvicorn main:app 引用


def _wait_port(port: int, tries: int = 100):
    """等 uvicorn 起监听，再开 WebView 窗口。"""
    import socket
    for _ in range(tries):
        try:
            socket.create_connection(("127.0.0.1", port), .2).close()
            return
        except OSError:
            time.sleep(.1)


def _pick_port(host: str, want: int) -> int:
    """优先用 want；被占用则让 OS 随机分配一个可用端口。"""
    import socket
    try:
        s = socket.socket()
        s.bind((host, want))
        s.close()
        return want
    except OSError:
        s = socket.socket()
        s.bind((host, 0))
        p = s.getsockname()[1]
        s.close()
        return p


if __name__ == "__main__":
    _srv = CFG.get("server") or {}
    _frozen = getattr(sys, "frozen", False)
    # 打包模式默认绑 0.0.0.0：手机 APK/局域网设备可直连本机服务端
    _host = _srv.get("listen_host") or ("0.0.0.0" if _frozen else "127.0.0.1")
    _want = _srv.get("listen_port", 8000)
    _port = _pick_port(_host, _want)   # 8000 被占则随机可用端口
    if _frozen:
        # 独立窗口模式：uvicorn 后台线程 + pywebview 前台窗（无浏览器外壳）
        # windowed 打包下 sys.stdout/err 为 None，先重定向防 logging 崩溃
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")

        def _serve():
            try:
                uvicorn.run(app, host=_host, port=_port,
                            log_level="warning", log_config=None)
            except Exception:
                with open("wasd_err.log", "a", encoding="utf-8") as f:
                    traceback.print_exc(file=f)

        threading.Thread(target=_serve, daemon=True).start()
        _wait_port(_port)
        try:
            import pyi_splash      # 关启动图：不关会卡死 WebView 窗口的创建
            pyi_splash.close()
        except Exception:
            pass
        import webview
        _title = "wasdSports" if _port == _want else f"wasdSports :{_port}"
        webview.create_window(
            _title, f"http://127.0.0.1:{_port}/",
            width=1280, height=840, min_size=(900, 600))
        webview.start()
        # 窗口已关：主线程同步登出远端会话。daemon 里的 uvicorn 来不及
        # 触发 shutdown 事件，不显式调用会导致服务端会话悬挂、
        # 下次登录撞 10121 设备锁
        try:
            state.close_session()
        except Exception:
            pass
    else:
        print(f"[wasdSports] serving on {_host}:{_port}")
        uvicorn.run(app, host=_host, port=_port)
