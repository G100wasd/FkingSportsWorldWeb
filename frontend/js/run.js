/* ---------- 跑步区域：拉点 -> 拟合 -> 提交 ---------- */
function clearOverlays() {
  overlays.concat(fitOverlays).forEach(o => o.setMap && o.setMap(null));
  overlays = []; fitOverlays = [];
}

function drawPoints(d) {
  overlays.forEach(o => o.setMap && o.setMap(null));
  overlays = [];
  d.points.forEach((p, i) => {
    overlays.push(new AMap_.CircleMarker({
      center: bdToLngLat(p.lat, p.lon), radius: 7, map, zIndex: 60,
      fillColor: "#e53e3e", fillOpacity: 1,
      strokeColor: "#fff", strokeWeight: 2,
    }));
    overlays.push(new AMap_.Text({
      text: (i + 1) + "." + (p.name || ""), map, zIndex: 60,
      position: bdToLngLat(p.lat, p.lon), offset: new AMap_.Pixel(0, -18),
      style: { background: "none", border: "none", color: "#c53030",
               "font-size": "11px", "font-weight": "bold",
               "text-shadow": "0 0 3px #fff, 0 0 3px #fff" },
    }));
  });
}

async function fetchPoints() {
  if (!sess) { setStatus("err", "请先登录"); return; }
  const c = readInputs();
  if (!c) { $("runarea-info").textContent = "经纬度无效"; return; }
  $("btn-points").disabled = true;
  $("btn-fit").disabled = true;
  $("runarea-info").textContent = "拉取打卡点中…";
  setRunBlock("busy");
  try {
    const d = await api("/api/points", { lat: c[0], lon: c[1] });
    clearOverlays();
    clearCtrls();
    drawPoints(d);
    map.setFitView(overlays);
    trackLocs = []; origLocs = []; lapIdx = []; snapIdx = [];
    lastFit = null;
    resetResultCard();
    $("btn-fit").disabled = false;
    $("btn-edit").disabled = false;
    $("btn-submit").disabled = true;
    editing = false;
    $("btn-edit").textContent = "调整轨迹";
    $("btn-edit").classList.remove("active");
    ["ctrl-row", "ctrl-wrow", "ctrl-rrow"]
      .forEach(id => $(id).style.display = "none");
    const sc = d.scoring;
    const scTxt = sc && sc.ok
      ? `计分跑已开启（minDistance=${sc.minDistance}）`
      : `计分跑被拒（${(sc && sc.error) || "?"}）`;
    $("runarea-info").innerHTML =
      `${scTxt}<br>` +
      `区域 <b>${d.area_name}</b>（runAreaId=${d.run_area_id}）下发 ${d.points.length} 个打卡点` +
      (d.stadium_name ? `，点位落在 <b>${d.stadium_name}</b>` : "") +
      `：<br>` + d.points.map((p, i) => `${i + 1}. ${p.name || ""}`).join("　");
  } catch (e) {
    fitError(e.message);
  } finally {
    $("btn-points").disabled = false;
    setRunBlock(sess ? "idle" : "off");
  }
}

let MAX_REFIT = 3;

function resetResultCard() {
  $("fit-summary").innerHTML =
    `<div class="hint">NO DATA — 先获取打卡点并拟合。</div>`;
  $("fit-detail").style.display = "none";
  $("stats-hint").style.display = "";
  const w = $("submit-warn");
  if (w) w.textContent = "";
  updateSubmitMode();
}

function clearDeferHint() {
  const e = $("submit-defer-hint");
  if (e) e.textContent = "";
}

/* 提交按钮的两种形态：
   - 伪装起跑在未来 → "延迟提交"：不需要先拟合，后台到点自己
     拉点→拟合→跑完→提交（服务端日志与真人同构）
   - 否则 → "提交记录"：要求已有拟合结果
   配置卡改日期/时间会触发重算，这里每次渲染和每 10s 复评一次。 */
function deferStartMs() {
  const ms = (typeof resolveStartMs === "function") ? resolveStartMs() : 0;
  return (ms && ms > Date.now() + 30000) ? ms : 0;
}

function updateSubmitMode() {
  if (_lockPrev) return;              // 排队锁定期间不改按钮
  const b = $("btn-submit");
  const defer = deferStartMs();
  const hasFit = !!(lastFit && lastFit.locs && lastFit.locs.length);
  if (defer && !hasFit) {
    b.textContent = "延迟提交";
    b.disabled = !sess || !readInputs();
    b.title = "到伪装起跑时刻后自动：拉点 → 拟合 → 跑完 → 提交";
  } else {
    b.textContent = defer ? "延迟提交" : "提交记录";
    b.disabled = !hasFit && !defer;
    b.title = "";
  }
}
setInterval(() => { if (!_lockPrev) updateSubmitMode(); }, 10000);

/* 提交时间戳警告：记录结束早于当前 >10min 时提交，服务端日志里
   "结束时刻"与"上传时刻"间隔过大，与真机跑完即传的行为不符，
   有被追缴风险。拟合渲染时评估 + 30s 周期复评（ staleness 只增
   不减，跨过阈值自动出现）；提交点击时再复查一次。 */
let _warnTimer = null;
function updateSubmitWarn() {
  const w = $("submit-warn");
  if (!w) return;
  const s = lastFit && lastFit.summary;
  let ago = null;
  if (s && s.start_ms && s.duration_s)
    ago = Math.round((Date.now() - s.start_ms - s.duration_s * 1000) / 60000);
  w.textContent = (ago != null && ago > 10)
    ? `⚠ 记录结束于 ${ago} 分钟前：时间戳与服务器日志对不上，提交可能被追缴`
    : "";
}

function renderFit(d, retried) {
  if (!d || !Array.isArray(d.locs) || !d.locs.length ||
      !Array.isArray(d.track_bd)) {
    console.error("fit resp:", d);
    fitError("拟合返回数据异常（轨迹点为空或格式不对），F12 控制台有原始响应");
    return;
  }
  clearCtrls();
  const row = p => Array.isArray(p)
    ? [p[0], p[1]] : [p.gLat ?? p.lat, p.gLng ?? p.lon];
  trackLocs = d.locs.map(row);
  origLocs = trackLocs.map(p => p.slice());
  lapIdx = d.locs.map(p => (Array.isArray(p) ? p[2] : 0) || 0);
  snapIdx = (d.snap_idx || []).slice().sort((a, b) => a - b)
                     .filter((v, i, a) => i === 0 || v !== a[i - 1]);
  fitOverlays.forEach(o => o.setMap && o.setMap(null));
  fitOverlays = [];
  fitOverlays.push(new AMap_.Polyline({
    path: d.track_bd.map(p => bdToLngLat(p[0], p[1])), map, zIndex: 30,
    strokeColor: "#4a5568", strokeWeight: 2, strokeStyle: "dashed",
    strokeOpacity: 0.9,
  }));
  drawTrackLaps();
  const st = d.locs[0], en = d.locs[d.locs.length - 1];
  fitOverlays.push(new AMap_.Marker({
    position: bdToLngLat(st[0], st[1]), map, zIndex: 70,
    label: { content: "起点", direction: "top" },
  }));
  fitOverlays.push(new AMap_.Marker({
    position: bdToLngLat(en[0], en[1]), map, zIndex: 70,
    label: { content: "终点", direction: "top" },
  }));
  const f = d.fit;
  lastFit = d;
  renderSummary(d.summary);
  renderDetail(d);
  $("fit-detail-json").textContent = JSON.stringify(d, null, 1);
  $("fit-detail").style.display = "";
  $("stats-hint").style.display = "none";
  updateSubmitMode();
  $("runarea-info").innerHTML =
    (retried ? `<span style="color:var(--warn)">重新拉点后拟合成功：</span><br>` : "") +
    `直道 ${f.straight.toFixed(0)}m + 半圆 r=${f.radius.toFixed(0)}m | 周长 ${f.perimeter.toFixed(0)}m | 轨迹 ${d.locs.length} 点<br>` +
    `误差 均=${f.mean_err.toFixed(1)}m 最大=${f.max_err.toFixed(1)}m | 边界余量 ${f.fence_margin.toFixed(1)}m | 锚点 ${snapIdx.length} 个`;
  if (editing) buildControls();
  flashBtnResult($("btn-fit"), true, "拟合轨迹", "拟合完成");
  updateSubmitWarn();
  if (!_warnTimer) _warnTimer = setInterval(updateSubmitWarn, 30000);
  // 若该 uuid 已在定时队列中（刷新/重进后恢复显示）
  $("btn-cancel-submit").style.display = "none";
  fetch("/api/submit/status").then(r => r.json()).then(st => {
    const j = (st.pending || []).find(j => j.run_uuid === d.run_uuid);
    if (j) watchQueue(d.run_uuid, j.fire_at);
  }).catch(() => {});
}

function fitError(msg) {
  $("runarea-info").innerHTML = `<span style="color:var(--red)">${msg}</span>`;
}

async function refetchAndRefit(prevErr, attempt) {
  $("btn-points").disabled = true;
  try {
    $("runarea-info").innerHTML =
      `<span style="color:var(--warn)">拟合无可行解（${prevErr}）<br>` +
      `自动重新获取打卡点并重试（第 ${attempt}/${MAX_REFIT} 次）…</span>`;
    const c = readInputs();
    if (!c) { fitError("经纬度无效"); return; }
    let d;
    try {
      d = await api("/api/points", { lat: c[0], lon: c[1] });
    } catch (e) {
      fitError(`重新获取打卡点失败：${e.message}<br>点位接口限流约 5 分钟 3 次，已达上限请稍后手动重试。`);
      return;
    }
    clearOverlays();
    clearCtrls();
    trackLocs = []; origLocs = []; lapIdx = []; snapIdx = [];
    lastFit = null;
    resetResultCard();
    drawPoints(d);
    try {
      renderFit(await api("/api/fit", { ...fitGoal(), reseed: true }), true);
    } catch (e2) {
      if (e2.message.includes("无可行解") && attempt < MAX_REFIT)
        return await refetchAndRefit(e2.message, attempt + 1);
      fitError(e2.message);
    }
  } finally {
    $("btn-points").disabled = false;
  }
}

async function doFit() {
  $("btn-fit").disabled = true;
  $("runarea-info").textContent = "拟合体育场跑道并生成轨迹中…";
  setRunBlock("busy");
  try {
    renderFit(await api("/api/fit", { ...fitGoal(), reseed: true }));
  } catch (e) {
    console.error(e);
    if (e.message.includes("无可行解")) await refetchAndRefit(e.message, 1);
    else fitError(e.message);
  } finally {
    $("btn-fit").disabled = false;
    setRunBlock(sess ? "idle" : "off");
  }
}

/* 账号切换时清空本地跑步态：地图叠加层、轨迹缓存、结果卡、
   排队倒计时——否则新账号会看到上一个账号的点位/轨迹/统计。 */
function clearRunState() {
  clearInterval(_queueTicker); clearInterval(_queuePoll);
  _queueTicker = _queuePoll = null;
  lockUI(false);
  $("btn-cancel-submit").style.display = "none";
  clearOverlays();
  clearCtrls();
  trackLocs = []; origLocs = []; lapIdx = []; snapIdx = [];
  lastFit = null;
  editing = false;
  $("btn-edit").textContent = "调整轨迹";
  $("btn-edit").classList.remove("active");
  ["ctrl-row", "ctrl-wrow", "ctrl-rrow"]
    .forEach(id => { const e = $(id); if (e) e.style.display = "none"; });
  $("btn-fit").disabled = true;
  $("btn-edit").disabled = true;
  resetResultCard();
  $("runarea-info").textContent = "";
}

/* 改配置后的重算：复用服务端缓存的跑道几何，只按新距离/用时/
   步幅等重新生成轨迹数据——不重拟合几何、不重拉打卡点，
   因此也不需要"无可行解"重试链。 */
async function recalc() {
  $("cfg-hint").innerHTML +=
    `<br><span style="color:var(--warn)">配置已更新，重算数据中…</span>`;
  try {
    renderFit(await api("/api/fit", fitGoal()));   // 不带 reseed：沿用原随机实现
  } catch (e) {
    fitError("按新配置重算失败：" + e.message);
  } finally {
    updateCfgHint();   // 整体重写 hint，黄字自然消失
  }
}

$("btn-points").onclick = fetchPoints;
$("btn-fit").onclick = doFit;
/* 排队期间全局锁：队列里的 job 绑定"这一次拟合"，期间改配置/重新
   拉点/再拟合都会让即将提交的载荷与界面不一致——直接禁用相关按钮，
   取消或提交完成后恢复各自原状态。 */
const LOCK_IDS = ["btn-auto", "m-auto", "btn-points", "btn-fit",
                  "btn-edit", "btn-cfg-save", "btn-manual"];
let _lockPrev = null;
function lockUI(on) {
  if (on) {
    if (_lockPrev) return;
    _lockPrev = {};
    LOCK_IDS.forEach(id => {
      const b = $(id); if (!b) return;
      _lockPrev[id] = b.disabled; b.disabled = true;
    });
  } else {
    if (!_lockPrev) return;
    const prev = _lockPrev; _lockPrev = null;
    LOCK_IDS.forEach(id => {
      const b = $(id); if (b && id in prev) b.disabled = prev[id];
    });
    updateAutoBtn();   // 自动跑按钮按最新 auto 位重算（提交后可能刚解锁）
  }
}

/* 定时提交队列 UI：按钮显示倒计时，旁挂"取消排队"；
   每 15s 轮询状态直到该 uuid 出队（执行完/被取消）。 */
let _queueTicker = null, _queuePoll = null;
function watchQueue(uuid, fireAt, deferred) {
  clearInterval(_queueTicker); clearInterval(_queuePoll);
  $("btn-cancel-submit").style.display = "";
  $("btn-submit").disabled = true;
  lockUI(true);
  // deferred 阶段倒计时到"开跑"，之后接力到真正的提交 job
  let label = deferred ? "开跑" : "自动提交", stage = "";
  const tick = () => {
    const left = Math.max(0, Math.round((fireAt - Date.now()) / 1000));
    $("btn-submit").textContent = stage ? stage
      : left > 0 ? `${label} ${fmtDur(left)}`
      : (deferred ? "开跑中…" : "提交中…");
  };
  tick();
  _queueTicker = setInterval(tick, 1000);
  _queuePoll = setInterval(async () => {
    try {
      const st = await fetch("/api/submit/status").then(r => r.json());
      const mine = (st.pending || []).find(j => j.run_uuid === uuid);
      if (mine) {
        // 到点执行中：按钮显示 拉取打卡点/拟合轨迹 等阶段
        stage = (mine.state === "failed") ? "" : (mine.stage || "");
        if (mine.state === "failed") {
          clearInterval(_queueTicker); clearInterval(_queuePoll);
          $("btn-cancel-submit").style.display = "";
          lockUI(false);
          $("btn-submit").disabled = false;
          updateSubmitMode();
          fitError(`定时任务失败：${esc(mine.error || "未知错误")}`);
        }
        return;
      }
      // 本 job 已出队：延迟任务会接力成一个真正的提交 job
      const next = (st.pending || []).find(j => j.from_deferred === uuid);
      if (next) {
        $("runarea-info").innerHTML =
          `<span style="color:var(--warn)">已开跑并完成拟合，` +
          `将于跑完后自动提交</span>`;
        watchQueue(next.run_uuid, next.fire_at, false);
        return;
      }
      clearInterval(_queueTicker); clearInterval(_queuePoll);
      $("btn-cancel-submit").style.display = "none";
      const lr = st.last;
      await refreshSessionFlags();   // 先刷 auto 位，再解锁按钮
      lockUI(false);
      if (lr && lr.run_uuid === uuid && lr.ok) {
        $("btn-submit").textContent = "✓ 已自动提交";
        $("runarea-info").innerHTML =
          `<span style="color:var(--green)">定时提交完成：` +
          `rrid=${lr.rrid} uuid=${uuid} ｜OBS ${lr.obs_ok}/2` +
          `${lr.verified ? " ｜已回读" : ""}</span>`;
      } else {
        $("btn-submit").disabled = false;
        updateSubmitMode();
      }
    } catch (_) {}
  }, 5000);
}
$("btn-cancel-submit").onclick = async () => {
  try { await api("/api/submit/cancel", {}); } catch (e) {
    $("runarea-info").innerHTML =
      `<span style="color:var(--red)">取消失败：${esc(e.message)}</span>`;
    return;
  }
  clearInterval(_queueTicker); clearInterval(_queuePoll);
  $("btn-cancel-submit").style.display = "none";
  lockUI(false);
  $("btn-submit").disabled = false;
  updateSubmitMode();
  $("runarea-info").innerHTML =
    `<span style="color:var(--warn)">已取消排队提交</span>`;
};

/* 延迟提交：伪装起跑在未来且还没拟合 —— 把锚点+目标+起跑时刻
   交给后台，到点自己走 拉点→拟合→跑完→提交。 */
async function submitDeferred(startMs) {
  const c = readInputs();
  if (!c) { fitError("经纬度无效"); return; }
  const g = fitGoal();
  const r = await api("/api/submit/deferred",
                      { lat: c[0], lon: c[1], dist: g.dist, dur: g.dur,
                        start_ms: startMs });
  const t = new Date(r.fire_at);
  const p = n => String(n).padStart(2, "0");
  clearDeferHint();
  flashBtnResult($("btn-submit"), true, "延迟提交", "已排队");
  $("runarea-info").innerHTML =
    `<span style="color:var(--warn)">已排入延迟提交：` +
    `${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())} 自动开跑` +
    `（拉点 → 拟合 → ${fmtDur(g.dur)} 后提交）</span>`;
  watchQueue(r.uuid, r.fire_at, true);
}

$("btn-submit").onclick = async () => {
  const defer = deferStartMs();
  const hasFit = !!(lastFit && lastFit.locs && lastFit.locs.length);
  if (!hasFit && !defer) return;
  updateSubmitWarn();   // 点击时再复查一次，跨过阈值立即显示
  $("btn-submit").disabled = true;
  $("btn-submit").textContent = "排队中…";
  setRunBlock("busy");
  try {
    if (defer && !hasFit) { await submitDeferred(defer); return; }
    const r = await api("/api/submit", {});
    if (r.scheduled) {
      const t = new Date(r.fire_at);
      const p = n => String(n).padStart(2, "0");
      flashBtnResult($("btn-submit"), true, "提交记录", "已排队");
      $("runarea-info").innerHTML =
        `<span style="color:var(--warn)">已加入定时提交队列：` +
        `将于 ${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())} ` +
        `自动提交（约 ${Math.max(1, Math.round(r.eta_s / 60))} 分钟后，` +
        `服务端视角 = 拉点→跑完→上传）</span>`;
      watchQueue(r.uuid, r.fire_at);
      return;
    }
    flashBtnResult($("btn-submit"), true, "提交记录");
    const obsTxt = r.obs_ok != null
      ? `｜OBS ${r.obs_ok}/${r.obs_total}` +
        (r.obs_ok >= r.obs_total ? "" : "（轨迹可能不完整）")
      : "";
    const verTxt = r.verified ? "｜服务端已回读" : "";
    $("runarea-info").innerHTML =
      `<span style="color:var(--green)">√ 提交成功 ` +
      `rrid=${r.rrid} uuid=${r.uuid}${obsTxt}${verTxt}</span>`;
    refreshSessionFlags();   // auto 位+锚点落盘后刷新自动跑解锁态
  } catch (e) {
    flashBtnResult($("btn-submit"), false,
                   defer && !hasFit ? "延迟提交" : "提交记录");
    $("btn-submit").disabled = false;
    $("runarea-info").innerHTML =
      `<span style="color:var(--red)">提交失败：${esc(e.message)}</span>`;
  } finally {
    setRunBlock(sess ? "idle" : "off");
  }
};
