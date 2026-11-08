"""定时提交队列：进程内 pending job + 到期调度执行。

时间线自洽模型：服务端日志应看到 points@m -> submit@m+n+延迟。
job 只在内存中——进程关闭即弃（用户授权语义：点了提交就等到点，
应用关了就当没发生过）；fire_at = 记录结束 + U(45,95)s，
~10% 长尾 +5~30min（真机上传延迟厚尾）。
"""

import datetime as dt
import json
import random
import threading
import time
import traceback

from uuid import uuid4

from ..domain import wire
from ..services import obs, records, submit
from . import pipeline, state


def now_ms() -> int:
    return int(dt.datetime.now().timestamp() * 1000)


jobs = {}               # run_uuid -> job（进程内，关进程即弃）
firing = set()          # 执行中标记
last_fired = None       # 最近一次执行结果（供 status 展示，易失）


def pending() -> dict:
    return jobs


def fire_delay_ms() -> int:
    """提交延迟：主体 U(45,95)s + 10% 长尾 +5~30min。"""
    d = random.uniform(45, 95)
    if random.random() < 0.10:
        d += random.uniform(300, 1800)
    return int(d * 1000)


def enqueue_submit(fit: dict) -> dict:
    """把拟合载荷挂入队列，返回 job。"""
    uuid = fit["run_uuid"]
    if uuid in jobs:
        return jobs[uuid]
    trk = fit["track"]
    end_ms = ((trk.get("startTime") or 0) +
              int(trk["locations"][-1]["totalTime"]) * 1000
              if trk.get("locations") else 0)
    fire_at = end_ms + fire_delay_ms()
    fire_at = max(fire_at, now_ms() + random.uniform(45, 95) * 1000)
    job = {"run_uuid": uuid, "kind": "submit", "stage": "待提交",
           "username": fit["username"],
           "body": fit["body"], "policy_ts": fit["policy_ts"],
           "track": trk, "five_point": fit["five_point"],
           "end_ms": end_ms, "fire_at": int(fire_at),
           "created_ms": now_ms(), "attempts": 0}
    jobs[uuid] = job
    return job


def enqueue_deferred(username: str, lat: float, lon: float,
                     dist: float, dur: int, start_ms: int) -> dict:
    """延迟提交：伪装起跑时刻在未来时，先排队等到那一刻，再由后台
    走 拉点 -> 拟合 -> 等跑完 -> 提交。服务端日志因此看到的是
    points@起跑时刻 → submit@起跑+时长+延迟，与真人完全同构。"""
    jid = "defer-" + str(uuid4()).upper()
    job = {"run_uuid": jid, "kind": "deferred", "stage": "待开跑",
           "username": username, "anchor": {"lat": lat, "lon": lon},
           "dist": dist, "dur": dur, "start_ms": int(start_ms),
           "fire_at": int(start_ms), "created_ms": now_ms(), "attempts": 0}
    jobs[jid] = job
    return job


def _fire_deferred(job: dict):
    """到达伪装起跑时刻：拉点 -> 拟合 -> 转成普通提交 job 继续排队。"""
    jid = job["run_uuid"]
    sess = state.get_session()
    job["stage"] = "拉取打卡点"
    pipeline.fetch_points(sess, job["anchor"]["lat"], job["anchor"]["lon"])
    job["stage"] = "拟合轨迹"
    # start_ms 不传：拟合内部锚定刚才那次真实拉点时刻
    pipeline.run_fit(sess, job["dist"], job["dur"], 0, True)
    fit = state.last["fit"]
    if not fit:
        raise RuntimeError("拟合未产出提交载荷")
    jobs.pop(jid, None)                  # 占位 job 退场，换成真正的提交 job
    nxt = enqueue_submit(fit)
    nxt["from_deferred"] = jid
    return nxt


def _fire_job(uuid: str):
    """执行一个到期 job：会话检查 -> 提交 -> OBS -> 回读 -> 出队。
    deferred 类型则先走 拉点+拟合，再转为提交 job 继续等。"""
    global last_fired
    job = jobs.get(uuid)
    if not job or uuid in firing:
        return
    firing.add(uuid)
    try:
        if job.get("username") != state.current_user():
            job["state"], job["last_error"] = "waiting_user", "账号不符/未登录"
            return
        if job.get("kind") == "deferred":
            try:
                _fire_deferred(job)
            except Exception as e:       # 拉点限流/拟合无可行解/网络
                traceback.print_exc()
                job["attempts"] = job.get("attempts", 0) + 1
                job["last_error"] = getattr(e, "detail", None) or str(e)
                job["stage"] = "开跑失败"
                if job["attempts"] >= 3:
                    job["state"] = "failed"
                else:                    # 退避重试：1min/2min（点位接口限流）
                    job["state"] = "pending"
                    job["fire_at"] = now_ms() + job["attempts"] * 60_000
            return
        try:
            sess = state.get_session()
            result = submit.submit_record(
                sess, job["body"], uuid, job["policy_ts"])
        except submit.SubmitRejected as e:
            job["state"], job["last_error"] = "failed", str(e)
            return
        except Exception as e:                 # 含 SubmitError(结果未知)/网络
            traceback.print_exc()
            job["attempts"] = job.get("attempts", 0) + 1
            job["last_error"] = str(e)
            if job["attempts"] >= 3:
                job["state"] = "failed"
            else:                              # 退避重试：1min/2min
                job["state"] = "pending"
                job["fire_at"] = now_ms() + job["attempts"] * 60_000
            return
        rrid = result["rrid"]
        obs_ok = 0
        try:
            time.sleep(1)
            obj = wire.build_obs_object(
                job["track"], rrid, uuid, sess.uid, job["five_point"])
            keys = wire.obs_keys(job["track"], rrid, uuid)
            payload = json.dumps(obj, separators=(",", ":"),
                                 ensure_ascii=False).encode("utf-8")
            obs_ok = obs.upload_both_keys(sess, keys, payload)
        except Exception:
            traceback.print_exc()
        state.set_auto(job["username"], True)   # 首次成功提交 → 解锁自动跑
        verified = False
        try:
            time.sleep(2)
            verified = bool(records.fetch_one_record(sess, rrid))
        except Exception:
            traceback.print_exc()
        jobs.pop(uuid, None)
        last_fired = {"run_uuid": uuid, "rrid": rrid, "obs_ok": obs_ok,
                      "verified": verified, "ok": True, "ts": now_ms()}
    finally:
        firing.discard(uuid)


def _submit_worker():
    """后台调度：~10s 扫描队列，到期即执行（晚交合法）。"""
    while True:
        time.sleep(10)
        try:
            now = now_ms()
            for uuid, j in list(jobs.items()):
                if j.get("fire_at", 0) <= now and \
                        j.get("state", "pending") in ("pending", "waiting_user"):
                    _fire_job(uuid)
        except Exception:
            traceback.print_exc()


def start_worker():
    threading.Thread(target=_submit_worker, daemon=True,
                     name="submit-worker").start()
