"""Android 跑步提交签名（移植 crypto/sign.rs）。

UploadSignEntity 31 字段固定顺序（+sportType=3 的 roomId）；
String.valueOf 字符串化；signature=MD5(忽略大小写排序拼接 + 盐)、
originalSign=区分大小写排序拼接（无 MD5 无盐）。
"""

import hashlib
import json

SALT = "2slhe02lsfiwowlcixisla_sls-_slaor"

# UploadSignEntity 字段声明顺序（31 个）
UPLOAD_SIGN_FIELD_ORDER = [
    "sportType", "totalTime", "totalDis", "speed", "startTime", "stopTime",
    "complete", "selDistance", "unCompleteReason", "getPrize", "status",
    "uuid", "uid", "avgStepFreq", "totalSteps", "selectedUnid", "calorie",
    "policy", "selRunTime", "validDis", "validTime", "useMobilityTools",
    "errorCode", "geeToken", "unauthorized", "themeId", "faceCheck",
    "goalId", "address", "avgPower", "totalAscent",
]


def _android_value(v):
    """Java String.valueOf 语义：String 原值、null→"null"、bool→"true/false"、
    整型浮点→整数串（Rust serde Number::to_string 同款）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, str):
        return v
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, int):
        return str(v)
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def _sign_map(body, has_room_id=False):
    m = [(k, _android_value(body[k])) for k in UPLOAD_SIGN_FIELD_ORDER
         if k in body]
    if has_room_id and "roomId" in body:
        m.append(("roomId", _android_value(body["roomId"])))
    return m


def _join(m, ignore_case):
    items = sorted(m, key=lambda kv: kv[0].lower() if ignore_case else kv[0])
    return "&".join(f"{k}={v}" for k, v in items)


def _filtered(body, has_room_id):
    return [(k, v) for k, v in _sign_map(body, has_room_id)
            if k.lower() != "signature"]


def original_sign(body, has_room_id=False):
    """区分大小写排序拼接（无 MD5，无盐）。"""
    return _join(_filtered(body, has_room_id), False)


def signature(body, has_room_id=False):
    """忽略大小写排序拼接 + 盐 的 MD5。"""
    q = _join(_filtered(body, has_room_id), True)
    return hashlib.md5((q + SALT).encode()).hexdigest()


def md5_url_sign(url):
    """HTTP 版 URL + 固定盐的 MD5（body 内嵌 sign 字段）。"""
    return hashlib.md5(
        (url.replace("https://", "http://", 1) + SALT).encode()).hexdigest()


if __name__ == "__main__":
    # crypto/sign.rs 测试向量自检
    sample = {
        "sportType": 3, "totalTime": 1000, "totalDis": 1200, "speed": 1200,
        "startTime": 1700000000000, "stopTime": 1700000001000,
        "complete": True, "selDistance": 1500, "unCompleteReason": 0,
        "getPrize": False, "status": 1, "uuid": "test-uuid", "uid": 13056447,
        "avgStepFreq": 134, "totalSteps": 1000, "selectedUnid": 57501,
        "calorie": 0, "policy": 0, "selRunTime": 0, "validDis": 1100,
        "validTime": 900, "useMobilityTools": 0, "errorCode": 0,
        "geeToken": "", "unauthorized": 0, "themeId": 0, "faceCheck": 1,
        "goalId": None, "address": "", "avgPower": 0, "totalAscent": 0,
        "roomId": 1001,
    }
    assert signature(sample, True) == "f2b958b2b9c8e99c4156076fbc72aabe"
    assert signature(sample, False) == "6187185669bbd60d0c9ff4148f33e16d"
    orig = original_sign(sample, True)
    assert orig.startswith(
        "address=&avgPower=0&avgStepFreq=134&calorie=0&complete=true"
        "&errorCode=0&faceChec")
    assert "goalId=null" in orig
    print("sign self-test ok")
