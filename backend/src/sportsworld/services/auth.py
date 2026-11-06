"""登录链路。

checkGeeUse 免验证码探测 -> [GT4 -> geevalidate] -> login。
成功返回 Session dict（uid/token/unid/name/weight）。
"""

import time
import uuid

import httpx

from ..config import CFG
from ..integration.client import check_business, envelope_request, get_field
from ..integration.envelope import EnvelopeSession, b64e, compact
from ..integration.gt4 import solve_gt4

HOST = (CFG.get("server") or {}).get("host", "https://run.gxapp.iydsj.com")
LOGIN_PATH = "/api/v70100/login"
LOGOUT_PATH = "/api/v6/user/logout"
GEECHECK_PATH = "/api/v65/security/checkGeeUse"
GEEVALIDATE_PATH = "/api/v70270/security/geevalidate"
CAPTCHA_ID_BIZ0 = "8c065103d81f5fd3efec8ad3e3a84c30"

# bizType=0 的 captchaId


class Session:
    """登录会话：业务字段 + 登出所需的协议态（identity / EnvelopeSession）。"""

    def __init__(self, biz: dict, identity: dict, envelope: EnvelopeSession):
        self.uid = get_field(biz, "uid") or 0
        self.token = get_field(biz, "token") or ""
        self.unid = str(get_field(biz, "unid") or "0")
        self.name = get_field(biz, "name") or ""
        self.weight = get_field(biz, "weight") or 0
        self.identity = identity
        self.envelope = envelope

    def logout(self) -> None:
        """POST /api/v6/user/logout 登出并打印结果。"""
        try:
            with httpx.Client(timeout=30.0) as client:
                envelope_request(
                    client, self.envelope, self.identity, "POST",
                    HOST + LOGOUT_PATH, "{}",
                    uid=self.uid, token=self.token,
                )
            print(f"[logout] 已请求登出 uid={self.uid}")
        except Exception as e:
            print(f"[logout] 失败: {e}")


def default_identity(device: dict | None = None) -> dict:
    """设备身份。device={platform,device_name,os_version} 来自前端探测；
    缺省按"非手机→默认安卓红米 22081212C/Android 14"全流程伪装。
    device_id/app_install_time 为空时逐请求随机/回退。"""
    device = device or {}
    platform = device.get("platform") or "android"
    if platform == "ios":
        dev_name, os_ver = device.get("device_name") or "iPhone", \
            device.get("os_version") or "26.5.2"
    else:
        platform = "android"
        dev_name, os_ver = device.get("device_name") or "22081212C", \
            device.get("os_version") or "14"
    return {
        "platform": platform,
        "device_id": "",
        "device_name": dev_name,
        "os_version": os_ver,
        "mac_address": _gen_mac(),
        "app_install_time": 0,
        "idfa": "",
    }


def _gen_mac() -> str:
    import secrets as _secrets
    return "08:a4:%02x:%02x:%02x:%02x" % tuple(
        _secrets.randbelow(256) for _ in range(4))


def stable_identity(username: str, device: dict | None = None) -> dict:
    """按账号持久化的设备指纹（对齐 Rust model.rs::load_identity）。

    device_id / app_install_time / mac_address 首次生成即落盘，
    此后该账号整个生命周期复用——逐请求/逐登录漂移是服务端设备
    画像里最刺眼的非真人指纹。platform/device_name/os_version
    同样首登即固化（真实手机不会换型号）。
    """
    from ..config import load as _cfg_load, save as _cfg_save
    cfg = _cfg_load()
    ids = cfg.setdefault("identities", {})
    rec = dict(ids.get(username) or {})
    if not rec:
        base = default_identity(device)
        now_ms = int(time.time() * 1000)
        platform = base["platform"]
        # 安装时间：android 30-180 天前，iOS 2-10 天前（对齐 Rust 语义）
        days = (30.0, 180.0) if platform == "android" else (2.0, 10.0)
        rec = {
            "platform": platform,
            "device_name": base["device_name"],
            "os_version": base["os_version"],
            "device_id": str(uuid.uuid4()).upper(),
            "mac_address": _gen_mac(),
            "app_install_time": now_ms - int(
                (days[0] + (days[1] - days[0])
                 * uuid.uuid4().int / (1 << 128)) * 86_400_000),
            "idfa": "",
        }
        ids[username] = rec
        _cfg_save(cfg)
    return dict(rec)


def gee_check(client: httpx.Client, session: EnvelopeSession, identity: dict, username: str) -> bool:
    """checkGeeUse 免验证码探测：data=true 可跳过 GT4。"""
    body = compact({
        "username": username,
        "uuid": str(uuid.uuid4()),
        "unid": 0,
        "type": 2,
    })
    try:
        out = envelope_request(client, session, identity, "POST", HOST + GEECHECK_PATH, body)
        skip = bool(out["business"].get("data"))
        print(f"[gee] checkGeeUse data={skip}（{'跳过 GT4' if skip else '需 GT4 流程'}）")
        return skip
    except Exception as e:
        print(f"[gee] checkGeeUse 失败（{e}），按需要验证码处理")
        return False


def login(username: str, password: str, identity: dict | None = None) -> Session:
    """完整登录：checkGeeUse -> [GT4 -> geevalidate] -> login。成功返回 Session。"""
    identity = identity or default_identity()
    session = EnvelopeSession()
    uuid_value = str(uuid.uuid4())

    with httpx.Client(timeout=30.0) as client:
        if gee_check(client, session, identity, username):
            print("[login] 免验证码路径：直接登录")
        else:
            # GT4 -> geevalidate；10003（验证失败）时重破滑块再验，最多 3 次
            validated = False
            for attempt in range(1, 4):
                print("[login] 开始 GT4 滑块验证")
                creds = solve_gt4(CAPTCHA_ID_BIZ0)
                print(f"[login] GT4 通过 lot_number={creds['lotNumber']}")
                gv_body = compact({
                    "lotNumber": creds["lotNumber"],
                    "captchaOutput": creds["captchaOutput"],
                    "passToken": creds["passToken"],
                    "genTime": creds["genTime"],
                    "isOffline": False,
                    "osType": 1,
                    "businessType": 0,
                    "uuid": uuid_value,
                    "username": username,
                })
                out = envelope_request(
                    client, session, identity, "POST", HOST + GEEVALIDATE_PATH, gv_body
                )
                gv_err = out["business"].get("error")
                if gv_err == 10000:
                    print("[geevalidate] 验证通过")
                    validated = True
                    break
                if gv_err == 10003:
                    print(f"[geevalidate] 验证失败（{attempt}/3），重试")
                    time.sleep(2)
                    continue
                print(f"[geevalidate] err={gv_err}（继续尝试登录）")
                validated = True  # 非 10003 的失败不阻塞登录
                break
            if not validated:
                raise RuntimeError("geevalidate 连续验证失败（10003），请重试")
            time.sleep(2)

        login_body = compact({
            "device_model": identity["device_name"],
            "os_version": identity["os_version"],
            "mac_address": identity["mac_address"],
            "imei": "",
            "loginType": 0,
            "username": username,
            "password": password,
            "uuid": uuid_value,
            "osType": "0",
        })
        extra = {"Authorization": "Basic " + b64e(f"{username}:{password}".encode())}
        out = envelope_request(
            client, session, identity, "POST", HOST + LOGIN_PATH, login_body,
            extra_headers=extra,
        )

    biz = check_business(out["business"])
    sess = Session(biz, identity, session)
    if not sess.uid or not sess.token:
        raise RuntimeError(f"登录响应缺 uid/token: {str(biz)[:200]}")
    print(f"[session] uid={sess.uid} token={sess.token[:8]}*** "
          f"unid={sess.unid} name={sess.name} weight={sess.weight}")
    return sess
