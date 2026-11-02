"""通用请求封装：body = 信封(明文, insert)，headerSign = 信封(header, observed)，
响应两层解密到业务 JSON；同一 EnvelopeSession 复用同一 keyDataFour。"""

import httpx

from .decrypt import decrypt_response, derive_paes_key
from .envelope import EnvelopeSession, build_envelope, now_ms
from .header import IOS_APP_VERSION, UA_IOS, build_ios_header


def envelope_request(
    client: httpx.Client,
    session: EnvelopeSession,
    identity: dict,
    method: str,
    url: str,
    body_plain: str,
    uid: int = -1,
    token: str = "",
    extra_headers: dict | None = None,
) -> dict:
    """发送信封请求，返回 {http_status, business, raw}。"""
    ts = now_ms()
    header_plain = build_ios_header(identity, uid, token, ts)
    header_env, _, _ = build_envelope(session, header_plain, "observed", ts)
    # body 时间戳 = header ts + 1（原生两次独立读取毫秒）
    body_env, kd, _ = build_envelope(session, body_plain, "insert", ts + 1)

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": UA_IOS,
        "appVersion": IOS_APP_VERSION,
        "headerSign": header_env,
    }
    headers.update(extra_headers or {})
    resp = client.request(method, url, headers=headers, content=body_env.encode("utf-8"))
    raw = resp.content
    key = derive_paes_key(*kd)
    business = decrypt_response(raw, key)
    return {"http_status": resp.status_code, "business": business, "raw": raw}


def check_business(biz: dict) -> dict:
    """error == 10000 -> biz；否则抛业务错误。"""
    err = biz.get("error") or 0
    if err == 10000:
        return biz
    msg = biz.get("message") or biz.get("msg") or "(无消息)"
    raise RuntimeError(f"业务错误 {err}: {msg}")


def get_field(biz: dict, name: str):
    """业务字段查找：先顶层，再 data 子对象。"""
    v = biz.get(name)
    if v is not None:
        return v
    data = biz.get("data")
    return data.get(name) if isinstance(data, dict) else None
