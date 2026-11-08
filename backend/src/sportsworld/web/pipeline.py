"""拉点 / 拟合的核心流程（与 HTTP 解耦）。

端点层和定时队列都调用这里：前者是用户点按钮，后者是"延迟提交"
到点后由后台线程自己走一遍 拉点 -> 拟合，两条路必须产出完全相同
的 state.last["fit"] 载荷，否则定时跑出来的记录和手动跑不同源。
"""

import datetime as dt
import json
import random
import traceback

from fastapi import HTTPException

from ..config import CFG
from ..domain import stadium, track, wire
from ..services import runarea, submit
from . import state

RUN = CFG.get("run") or {}   # 与 api_config 共享同一字典对象：保存配置即生效


def fetch_points(sess, lat: float, lon: float) -> dict:
    """按锚点拉实时打卡点：围栏 -> 计分跑申请 -> 取点，结果存入 state.last。"""
    state.last["anchor"] = (lat, lon)
    state.last["fit"] = None      # 新点位使旧拟合作废，防止误提交过期数据
    state.last["ring"] = None     # 点位变了，跑道几何缓存同时作废
    state.last["seed"] = None     # 新点位 = 新一次跑，重掷随机实现
    if state.last["fences"] is None:
        try:
            state.last["fences"] = runarea.fetch_fences(sess)
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(502, f"拉取围栏失败: {e}")
    try:
        # 真实当前时间：假正午戳与请求日志时间矛盾是审计指纹
        state.last["points_ts"] = int(dt.datetime.now().timestamp() * 1000)
        state.last["area"] = runarea.fetch_area_points(
            sess, state.last["fences"], lat, lon,
            ts_ms=state.last["points_ts"])
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(502, f"拉取打卡点失败: {e}")
    area = state.last["area"]
    sel = next((a for a in area["areas"] if a["idx"] == area["area_idx"]), None)
    hit = next((a for a in area["areas"]
                if a["idx"] == area["stadium_idx"]), None)
    return {
        "area_name": sel["name"] if sel else "(默认区域)",
        "stadium_name": hit["name"] if hit else "",
        "run_area_id": area["area_run_area_id"],
        "points": [{"name": p.get("pointName"), "isFixed": p.get("isFixed"),
                    "lat": p.get("lat"), "lon": p.get("lon")}
                   for p in area["points"]],
        "scoring": area["scoring"],
    }


def run_fit(sess, dist: float = 0.0, dur: int = 0,
            start_ms: int = 0, reseed: bool = False) -> dict:
    """对暂存的打卡点做体育场拟合 + 轨迹生成（纯本地，不触发限流）。"""
    if state.last["area"] is None:
        raise HTTPException(409, "请先获取打卡点")
    area = state.last["area"]
    dist = dist or RUN.get("default_dist", 2200.0)
    dur = dur or RUN.get("default_dur", 720)
    if not (100.0 <= dist <= 50000.0) or not (30 <= dur <= 14400):
        raise HTTPException(400, "目标距离/用时超出合理范围")
    # 起跑时刻：锚定拉点时刻（真实流程=拉到点后开跑）。
    # 定时队列已接管提交时机——记录结束在未来合法，调度器会
    # 等到 end+delay 再发；未拉点/未传参才兜底回拨一个已完成窗口
    now_ms = int(dt.datetime.now().timestamp() * 1000)
    start_ms = start_ms or (
        state.last["points_ts"] if state.last["points_ts"]
        else now_ms - (dur + 60) * 1000)
    try:
        # 跑道几何只由点位+围栏决定：首次拟合后缓存，改配置后的重算
        # 直接复用同一条跑道（省掉 scipy 多初值搜索，也不会再冒出
        # "无可行解"——那是几何阶段的失败，与距离/用时无关）
        if state.last["ring"] is None:
            state.last["ring"] = stadium.make_stadium_ring(
                area["points_bd"], area["ref_center"], area["stadium_ring"])
        dense, arcs, center, fit = state.last["ring"]
        # 种子固定 → 改配置重算时轨迹形状不跳变（抖动/漂移同一实现），
        # 只有配置本身引起的差异；手动拟合/自动跑才重掷
        if reseed or state.last["seed"] is None:
            state.last["seed"] = random.randrange(1 << 62)
        trk = track.build_track(
            dist, dur, state.last["seed"], area["points_bd"],
            fence_ring=area["stadium_ring"], ring=(dense, arcs, center),
            start_ms=start_ms)
        locs = trk["locations"]
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(502, f"拟合/轨迹生成失败: {e}")
    if state.last["anchor"]:
        try:
            state.save_anchor(state.current_user(),
                              state.last["anchor"][0], state.last["anchor"][1])
        except Exception:
            traceback.print_exc()   # 锚点持久化失败不影响拟合结果
    perim = fit["perimeter"] or 1.0
    total_dis = float(locs[-1]["totalDis"]) if locs else 0.0
    total_time = int(locs[-1]["totalTime"]) if locs else 0
    speed_win, step_win = wire.submit_windows(trk)
    five_point = wire.five_point_wrapper(
        area["points"], trk,
        run_area_id=area.get("meta_run_area_id", -1),
        geo_fences=area.get("geo_fences") or [],
        freedom_show_fence=area.get("freedom_show_fence", False))
    scoring = area.get("scoring") or {}
    record_body, run_uuid = submit.build_record_body(
        trk, uid=sess.uid,
        selected_unid=int(sess.unid) if str(sess.unid).isdigit() else 0,
        policy=scoring.get("policy") or 0,
        min_distance=int(scoring.get("minDistance") or 0),
        face_check=RUN.get("face_check", 0),
        five_point_json=five_point,
        address=RUN.get("address", ""))
    # 提交载荷整份暂存：/api/submit 必须原样重放同一 uuid/body（runef 依赖）；
    # track/five_point 供 OBS 对象复用（fixed_point_json 与提交体同源）
    state.last["fit"] = {"body": record_body, "run_uuid": run_uuid,
                         "policy_ts": scoring.get("policy_ts") or "",
                         "username": state.current_user(), "track": trk,
                         "five_point": five_point}
    return {
        "track_bd": fit["track_bd"],
        "locs": [[q["gLat"], q["gLng"], int(q["totalDis"] // perim)]
                 for q in locs],
        "locs_full": locs,
        "snap_idx": [i for i, q in enumerate(locs) if q.get("snapped")],
        "speedPerTenSec": trk["speedPerTenSec"],
        "stepsPerTenSec": trk["stepsPerTenSec"],
        "segments": trk["segments"],
        "speed_windows": speed_win,
        "step_windows": step_win,
        "laps": wire.build_laps(trk, trk["startTime"]),
        "five_point": json.loads(five_point),
        "record_body": record_body,
        "run_uuid": run_uuid,
        "summary": {
            "distance_m": round(total_dis, 1),
            "duration_s": total_time,
            "start_ms": trk["startTime"] or None,
            "pace_s_per_km": round(total_time / (total_dis / 1000.0), 1)
                             if total_dis > 0 else None,
            "avg_speed_mps": round(total_dis / total_time, 3)
                             if total_time > 0 else None,
            "point_count": len(locs),
            "lap_count": int(total_dis // perim) + 1 if total_dis > 0 else 0,
            "snapped": sum(1 for q in locs if q.get("snapped")),
            "total_steps": trk["totalSteps"],
            "step_freq": trk["avgStepFreq"],
            "max_step_freq": trk.get("maxStepFreq"),
            "max_stride": trk.get("maxStride"),
            "calorie": trk["calorie"],
            "avg_power": trk["avgPower"],
            "ascent_m": trk["totalAscent"],
            "valid_dis_m": round(trk["validDistance"], 1),
            "valid_time_s": trk["validTime"],
            "pace_milli": trk["paceMilli"],
        },
        "fit": {
            "cx_bd": fit["cx_bd"],
            "angle_deg": fit["angle"] * 180 / 3.141592653589793,
            "straight": fit["straight"], "radius": fit["radius"],
            "perimeter": fit["perimeter"], "point_errors": fit["point_errors"],
            "mean_err": fit["mean_err"], "max_err": fit["max_err"],
            "fence_margin": fit["fence_margin"], "converged": fit["converged"],
            "alt_solutions": fit["alt_solutions"],
        },
    }
