"""请求头明文构造（iOS 形态，固定键序紧凑 JSON）。"""

import uuid

from .envelope import compact, md5_hex

IOS_APP_VERSION = "7.3.40"
UA_IOS = "SWCampus/7.3.40 (iPhone; iOS 18.1; Scale/3.00)"

# tokenSign 固定后缀
TOKEN_SIGN_SUFFIX = "2slhe02lsfiwowlcixisla_sls-_slaor"


def token_sign(uid: int, token: str, ts_ms: int) -> str:
    """SWEncryption.signObject:suffix: sortedQueryString + suffix 的 MD5。"""
    query = f"timeStamp={ts_ms}&token={token}&uid={uid}"
    return md5_hex((query + TOKEN_SIGN_SUFFIX).encode("utf-8"))


def build_ios_header(identity: dict, uid: int, token: str, ts_ms: int) -> str:
    """iOS 请求头明文（_fillFullHeaders 字段序）。uid<1/无 token 时省略对应字段。"""
    device_id = identity.get("device_id") or str(uuid.uuid4()).upper()
    install = identity.get("app_install_time") or ts_ms - 3 * 86_400_000
    m: dict = {
        "osType": "1",
        "DeviceId": device_id,
        "deviceName": identity["device_name"],
        "CustomDeviceId": f"{device_id}_iOS_sportsWorld_campus",
        "osVersion": identity["os_version"],
    }
    if identity.get("idfa"):
        m["IDFA"] = identity["idfa"]
    m.update({
        "logicPixel": "360x640",
        "physicPixel": "1080x1920",
        "cpuModel": "x86_64",
        "appVersion": IOS_APP_VERSION,
        "isRoot": False,
        "appInstallTime": install,
        "nonce": str(uuid.uuid4()).upper(),
    })
    if uid >= 1:
        m["uid"] = uid
    if token:
        m["token"] = token
    m["timeStamp"] = ts_ms
    m["studentId"] = uid if uid >= 1 else 0
    if uid >= 1 and token:
        m["tokenSign"] = token_sign(uid, token, ts_ms)
    return compact(m)
