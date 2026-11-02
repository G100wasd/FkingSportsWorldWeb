"""电子围栏 + 实时打卡点。

- 围栏：POST /api/v1/getGeoFenceForRun -> data.geoFences[]（points[]，BD-09 系）
- 点位：POST /api/v560/get/1/distance/1（sportType=4，按锚点+unid 返回当天打卡点）
"""

import json
import math
import uuid

import httpx

from ..integration.client import check_business, envelope_request, get_field
from ..integration.envelope import build_envelope, compact, md5_hex, now_ms
from .auth import HOST, Session

FENCE_PATH = "/api/v1/getGeoFenceForRun"
POINTS_PATH = "/api/v560/get/1/distance/1"

# 与 Android 签名盐同源
SALT = "2slhe02lsfiwowlcixisla_sls-_slaor"

X_PI = math.pi * 3000.0 / 180.0


def md5_url_sign(url: str) -> str:
    """HTTP 版 URL + 固定盐的 MD5（body 内嵌 sign 字段）。"""
    return md5_hex((url.replace("https://", "http://", 1) + SALT).encode())


def gcj02_to_bd09(lat: float, lng: float) -> tuple[float, float]:
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * X_PI)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * X_PI)
    return z * math.sin(theta) + 0.006, z * math.cos(theta) + 0.0065


def bd09_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    x, y = lng - 0.0065, lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * X_PI)
    return z * math.sin(theta), z * math.cos(theta)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def point_xy(p: dict) -> tuple[float, float] | None:
    """点位坐标 -> (lat, lon) BD 系。lat/lon 缺失或为 0 时回退 glat/glon 转 BD。"""
    lat = _num(p.get("lat") or p.get("latitude"))
    lon = _num(p.get("lon") or p.get("lng") or p.get("longitude"))
    if lat and lon:
        return lat, lon
    glat = _num(p.get("glat"))
    glon = _num(p.get("glon") or p.get("glng"))
    if glat and glon:
        return gcj02_to_bd09(glat, glon)
    return None


def _normalize_data(biz: dict) -> dict:
    """data 字段若为 JSON 字符串则解析（与 Rust normalize_business_data 同语义）。"""
    data = biz.get("data")
    if isinstance(data, str) and data.strip().startswith(("{", "[")):
        biz = dict(biz)
        biz["data"] = json.loads(data)
    return biz


def fetch_geo_fence(sess: Session) -> dict:
    """返回 {"polygons": [[(lat,lon),...], ...], "run_area_id": int}。"""
    body = compact({"geoFenceUpdateTime": 0})
    with httpx.Client(timeout=30.0) as client:
        out = envelope_request(
            client, sess.envelope, sess.identity, "POST",
            HOST + FENCE_PATH, body, uid=sess.uid, token=sess.token,
        )
    biz = _normalize_data(check_business(out["business"]))
    run_area_id = get_field(biz, "runAreaId")
    try:
        run_area_id = int(run_area_id)
    except (TypeError, ValueError):
        run_area_id = -1
    polygons = []
    raws = []
    for fence in get_field(biz, "geoFences") or []:
        ring = [xy for p in fence.get("points", []) if (xy := point_xy(p))]
        if ring:
            polygons.append(ring)
        raws.append(fence)
    return {"polygons": polygons, "run_area_id": run_area_id, "raw": raws}


def fetch_points(sess: Session, lat: float, lon: float, run_area_id: int | None = None) -> list[dict]:
    """拉取锚点附近实时打卡点（300s 服务端限流，勿频繁调用）。"""
    start_ms = now_ms()
    runec_input = f"{sess.uid}{lon:.6f}{lat:.6f}{(start_ms // 1000) * 1000}"
    runec, _, _ = build_envelope(sess.envelope, runec_input, "observed", start_ms)
    body = {
        "sportType": 4,
        "longitude": lon,
        "latitude": lat,
        "sign": md5_url_sign(HOST + POINTS_PATH),
        "uuid": str(uuid.uuid4()),
        "selectedUnid": sess.unid,
        "runec": runec,
    }
    if run_area_id is not None and run_area_id >= 0:
        body["runAreaId"] = str(run_area_id)
    with httpx.Client(timeout=30.0) as client:
        out = envelope_request(
            client, sess.envelope, sess.identity, "POST",
            HOST + POINTS_PATH, compact(body), uid=sess.uid, token=sess.token,
        )
    biz = _normalize_data(check_business(out["business"]))
    pts = get_field(biz, "pointsResModels") or []
    if not pts:
        raise RuntimeError("点位接口未返回有效点位列表")
    return pts


def points_bd(points: list[dict]) -> list[tuple[float, float]]:
    return [xy for p in points if (xy := point_xy(p))]
