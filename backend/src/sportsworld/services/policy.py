"""跑步策略：POST /api/v70103/runModePolicy。

body：{"runMode":1,"ruleUpdateTime":0,"geoFenceUpdateTime":0,"selectUnid":<unid>,"operateType":0}
返回 data.runRuleModel.minDistance（提交 selDistance）与 data.policy。
runMode 取值依学校配置而异（原仓库固定 1），探测时可传不同值对比。
"""

import httpx

from ..integration.client import check_business, envelope_request, get_field
from ..integration.envelope import compact
from .auth import HOST, Session
from .geo import point_xy

POLICY_PATH = "/api/v70103/runModePolicy"


def fetch_policy(sess: Session, run_mode: int = 1, ts_ms: int | None = None) -> dict:
    """返回规范化后的业务 JSON（data 字符串已解析）。ts_ms 可覆盖请求时间戳。"""
    body = compact({
        "runMode": run_mode,
        "ruleUpdateTime": 0,
        "geoFenceUpdateTime": 0,
        "selectUnid": int(sess.unid) if str(sess.unid).isdigit() else 0,
        "operateType": 0,
    })
    with httpx.Client(timeout=30.0) as client:
        out = envelope_request(
            client, sess.envelope, sess.identity, "POST",
            HOST + POLICY_PATH, body, uid=sess.uid, token=sess.token,
            ts_ms=ts_ms,
        )
    biz = check_business(out["business"])
    data = biz.get("data")
    if isinstance(data, str):
        import json
        try:
            biz = dict(biz)
            biz["data"] = json.loads(data)
        except json.JSONDecodeError:
            pass
    return biz


def extract_must_points(biz: dict) -> list[tuple[float, float]]:
    """策略响应 data 中防御式提取必经点列表（字段名不确定，逐个尝试）。"""
    points: list[tuple[float, float]] = []
    for name in [
        "pointList", "runPointList", "mustPointList", "passPointList", "mustPoints",
        "passPoints", "runPoints", "points", "nodeList", "checkPointList",
    ]:
        arr = get_field(biz.get("data") or {}, name) or get_field(biz, name)
        if not isinstance(arr, list):
            continue
        for p in arr:
            xy = point_xy(p) if isinstance(p, dict) else None
            if xy and xy not in points:
                points.append(xy)
    return points


def policy_summary(biz: dict) -> dict:
    data = biz.get("data") if isinstance(biz.get("data"), dict) else {}
    rule = get_field(biz, "runRuleModel") or {}
    return {
        "timestamp": get_field(biz, "timestamp") or data.get("timestamp"),
        "policy": get_field(biz, "policy") or data.get("policy"),
        "minDistance": rule.get("minDistance") or get_field(biz, "minDistance"),
        "validTime": rule.get("validTime") or get_field(biz, "validTime"),
        "must_points": extract_must_points(biz),
    }
