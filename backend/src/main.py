"""Web 后端入口：登录 API + 托管前端页面。

运行（在 backend/src 目录下）:
    python main.py            # 或 python -m uvicorn main:app --port 8000
然后浏览器访问 http://127.0.0.1:8000
"""

import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from prompts import DEFAULT_PASSWORD, DEFAULT_USERNAME
from sportsworld.services.auth import login

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="NekoSportsWorld Web")

# 单用户本地应用：只保留一个登录会话；锁保护登录/登出竞态
_lock = threading.Lock()
_session = None


class LoginBody(BaseModel):
    username: str
    password: str


def _sess_info(sess):
    return {
        "logged_in": sess is not None,
        "uid": sess.uid if sess else 0,
        "name": sess.name if sess else "",
        "unid": sess.unid if sess else "",
    }


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/defaults")
def api_defaults():
    """前端输入框默认值（凭据只存在 prompts.py 一处）。"""
    return {"username": DEFAULT_USERNAME, "password": DEFAULT_PASSWORD}


@app.get("/api/session")
def api_session():
    return _sess_info(_session)


@app.post("/api/login")
def api_login(body: LoginBody):
    global _session
    try:
        sess = login(body.username.strip(), body.password)
    except Exception as e:
        raise HTTPException(502, f"登录失败: {e}")
    with _lock:
        old, _session = _session, sess
    if old is not None:
        old.logout()  # 顶号前登出旧会话
    return _sess_info(sess)


@app.post("/api/logout")
def api_logout():
    global _session
    with _lock:
        sess, _session = _session, None
    if sess is not None:
        sess.logout()
    return _sess_info(None)


@app.on_event("shutdown")
def _on_shutdown():
    # 后端进程退出时自动登出，避免账号残留登录态触发 10121
    api_logout()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
