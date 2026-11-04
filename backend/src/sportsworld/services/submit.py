"""跑步记录提交：body 组装 + POST /api/v70260/runnings/save/record。

提交头按会话身份统一：手机→实测设备，非手机→默认安卓
（runes/runef/headerSign 双层信封 + nonce/timeStamp/tokenSign 附加头）。
"""

import math
import uuid as _uuid

from ..domain import wire
from ..integration.client import (check_business, envelope_request,
                                  get_field)
from ..integration.envelope import compact
from ..integration.sign import original_sign, signature
from .auth import HOST

RECORD_PATH = "/api/v70260/runnings/save/record"


class SubmitRejected(RuntimeError):
    """服务端明确拒绝（HTTP 401 或业务错误码）——按失败原因可修正后重试。"""


class SubmitError(RuntimeError):
    """传输/解密类失败——结果未知，可能已被服务端接收，勿盲目重试。"""


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


def submit_record(sess, body: dict, run_uuid: str,
                  policy_ts, host: str = HOST) -> dict:
    """POST save/record。返回 {rrid, uuid, http_status, complete}。

    sess: auth.Session（uid/token/identity/envelope）。
    policy_ts: 计分跑策略时间戳（scoring.policy_ts）。
    run_uuid 与 body["uuid"]、runef 必须同源（拟合时生成的那份）。
    """
    runes = f"{policy_ts}{sess.uid}"
    runef = f"{run_uuid}{body['startTime']}"
    try:
        with httpx.Client(timeout=30.0) as client:
            out = envelope_request(
                client, sess.envelope, sess.identity, "POST",
                host + RECORD_PATH, compact(body),
                uid=sess.uid, token=sess.token,
                extra_headers={"runes": runes, "runef": runef},
                include_extras=True)
    except Exception as e:
        raise SubmitError(f"提交请求/解密失败: {e}") from e
    if out["http_status"] == 401:
        raise SubmitRejected("提交被拒绝 (HTTP 401)：登录态失效，请重新登录")
    try:
        biz = check_business(out["business"])
    except RuntimeError as e:
        raise SubmitRejected(f"提交被拒绝：{e}") from e
    rrid_raw = get_field(biz, "rrid")
    try:
        rrid = int(rrid_raw or 0)
    except (TypeError, ValueError):
        rrid = 0
    if rrid <= 0:
        raise SubmitError(f"提交未返回 rrid: {str(biz)[:200]}")
    return {"rrid": rrid, "uuid": run_uuid,
            "http_status": out["http_status"],
            "complete": get_field(biz, "complete")}
