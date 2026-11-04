"""跑步记录提交体组装（移植 api/submit.rs 的 body 部分）。

只构建 body + 签名，不发送；POST /api/v70260/runnings/save/record
需要 Android 头 + 双层信封（headerSign/runes/runef），属下一步。
"""

import math
import uuid as _uuid

from ..domain import wire
from ..integration.sign import original_sign, signature

RECORD_PATH = "/api/v70260/runnings/save/record"


def _r0(x):
    return int(math.floor(x + 0.5)) if x >= 0 else int(math.ceil(x - 0.5))


def build_record_body(trk, *, uid, selected_unid, policy, min_distance,
                      face_check, five_point_json, address, run_uuid=None):
    """组装 save/record 提交体。返回 (body, run_uuid)。

    five_point_json 为 wire.five_point_wrapper 的字符串；
    为空时按协议不写入该键。
    """
    total_time = trk["totalTime"]
    total_dis = trk["totalDistance"]
    start_ms = trk["startTime"]
    run_uuid = (run_uuid or str(_uuid.uuid4())).upper()
    speed_win, step_win = wire.submit_windows(trk)

    body = {
        "allLocJson": "",
        "sportType": 1,
        "policy": policy,
        "totalTime": total_time,
        "startTime": start_ms,
        "stopTime": start_ms + total_time * 1000,
        "getPrize": False,
        "status": 0,
        "uuid": run_uuid,
        "uid": uid,
        "selectedUnid": selected_unid,
        "selRunTime": total_time,
        "selDistance": min_distance,
        "totalDis": _r0(total_dis),
        "speed": trk["paceMilli"],
        "validDis": _r0(trk["validDistance"]),
        "validTime": trk["validTime"],
        "complete": True,
        "unCompleteReason": 0,
        "calorie": trk["calorie"],
        "totalSteps": trk["totalSteps"],
        "avgStepFreq": trk["avgStepFreq"],
        "useMobilityTools": 0,
        "faceCheck": face_check,
        "totalAscent": _r0(trk["totalAscent"]),
        "avgPower": trk["avgPower"],
        "speedPerTenSec": speed_win,
        "stepsPerTenSec": step_win,
        "isUpload": False,
        "more": False,
        "latitude": trk["startLatitude"],
        "longitude": trk["startLongitude"],
        "maxRunTime": 0,
        "minSteps": 0,
        "errorCode": 0,
        "geeToken": "",
        "unauthorized": 0,
        "themeId": 0,
        "goalId": None,
        "address": (address or "").strip(),
    }
    if five_point_json:
        body["fivePointJson"] = five_point_json
    body["signature"] = signature(body, False)
    body["originalSign"] = original_sign(body, False)
    return body, run_uuid
