"""请求头明文构造（iOS/Android 双形态，固定键序紧凑 JSON）。"""

import uuid

from .envelope import compact, md5_hex

IOS_APP_VERSION = "7.3.40"
ANDROID_APP_VERSION = "7.3.70"
UA_IOS = "SWCampus/7.3.40 (iPhone; iOS 18.1; Scale/3.00)"
UA_ANDROID = ("Mozilla/5.0 (Linux; Android 14; 22081212C "
              "Build/UKQ1.231003.002) AppleWebKit/537.36 SWCampus/7.3.70")

# tokenSign 固定后缀
TOKEN_SIGN_SUFFIX = "2slhe02lsfiwowlcixisla_sls-_slaor"


def token_sign(uid: int, token: str, ts_ms: int) -> str:
    """SWEncryption.signObject:suffix: sortedQueryString + suffix 的 MD5。"""
    query = f"timeStamp={ts_ms}&token={token}&uid={uid}"
    return md5_hex((query + TOKEN_SIGN_SUFFIX).encode("utf-8"))


def build_ios_header(identity: dict, uid: int, token: str,
                     ts_ms: int) -> tuple[str, dict]:
    """iOS 请求头明文（_fillFullHeaders 字段序）。uid<1/无 token 时省略对应字段。
    返回 (紧凑JSON, 附加HTTP头: nonce/timeStamp/tokenSign)。"""
    device_id = identity.get("device_id") or str(uuid.uuid4()).upper()
    install = identity.get("app_install_time") or ts_ms - 3 * 86_400_000
    nonce = str(uuid.uuid4()).upper()
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
        "nonce": nonce,
    })
    if uid >= 1:
        m["uid"] = uid
    if token:
        m["token"] = token
    m["timeStamp"] = ts_ms
    m["studentId"] = uid if uid >= 1 else 0
    sign = token_sign(uid, token, ts_ms)
    if uid >= 1 and token:
        m["tokenSign"] = sign
    return compact(m), {"nonce": nonce, "timeStamp": str(ts_ms),
                        "tokenSign": sign}


def build_android_header(identity: dict, uid: int, token: str,
                         ts_ms: int) -> tuple[str, dict]:
    """Android 请求头明文（NativeNetworkCommonHeaders.build 键集，osType="0"）。

    返回 (紧凑JSON, 附加HTTP头: nonce/timeStamp/tokenSign)——
    普通调用丢弃 extras，save/record 提交时随 HTTP 头发送。"""
    device_id = identity.get("device_id") or str(uuid.uuid4()).upper()
    install = (identity.get("app_install_time")
               or ts_ms - 90 * 86_400_000)      # Android 惯例回退 90 天
    nonce = str(uuid.uuid4()).upper()
    m: dict = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "appVersion": ANDROID_APP_VERSION,
        "osType": "0",
        "DeviceId": device_id,
        "osVersion": identity["os_version"],
        "deviceName": identity["device_name"],
        "IMEI": "",
        "logicPixel": "1080x2400",
        "physicPixel": "1080x2400",
        "androidId": "",
        "blMac": "",
        "wifiMac": "",
        "cpuModel": "arm64-v8a",
        "isRoot": False,
        "appUpdateTime": install,
        "appInstallTime": install,
        "nonce": nonce,
        "timeStamp": ts_ms,
        "CustomDeviceId": f"{device_id}_android_sportsWorld_campus",
        "uid": uid,
        "token": token,
        "studentId": uid,
    }
    sign = token_sign(uid, token, ts_ms)
    m["tokenSign"] = sign
    return compact(m), {"nonce": nonce, "timeStamp": str(ts_ms),
                        "tokenSign": sign}


def build_header(identity: dict, uid: int, token: str,
                 ts_ms: int) -> tuple[str, dict]:
    """按 identity["platform"] 选择头形态；统一返回 (明文, extras)。"""
    if (identity or {}).get("platform") == "android":
        return build_android_header(identity, uid, token, ts_ms)
    return build_ios_header(identity, uid, token, ts_ms)
