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
  $("btn-submit").disabled = true;
  $("btn-submit").textContent = "提交记录";
  const w = $("submit-warn");
  if (w) w.textContent = "";
}

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
  if (d.submitted) {
    $("btn-submit").disabled = true;
    $("btn-submit").textContent = "✓ 已提交";
  } else {
    $("btn-submit").disabled = !d.locs.length;
    $("btn-submit").textContent = "提交记录";
  }
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
      renderFit(await api("/api/fit", fitGoal()), true);
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
    renderFit(await api("/api/fit", fitGoal()));
  } catch (e) {
    console.error(e);
    if (e.message.includes("无可行解")) await refetchAndRefit(e.message, 1);
    else fitError(e.message);
  } finally {
    $("btn-fit").disabled = false;
    setRunBlock(sess ? "idle" : "off");
  }
}

$("btn-points").onclick = fetchPoints;
$("btn-fit").onclick = doFit;
/* 定时提交队列 UI：按钮显示倒计时，旁挂"取消排队"；
   每 15s 轮询状态直到该 uuid 出队（执行完/被取消）。 */
let _queueTicker = null, _queuePoll = null;
function watchQueue(uuid, fireAt) {
  clearInterval(_queueTicker); clearInterval(_queuePoll);
  $("btn-cancel-submit").style.display = "";
  $("btn-submit").disabled = true;
  const tick = () => {
    const left = Math.max(0, Math.round((fireAt - Date.now()) / 1000));
    $("btn-submit").textContent =
      left > 0 ? `自动提交 ${fmtDur(left)}` : "提交中…";
  };
  tick();
  _queueTicker = setInterval(tick, 1000);
  _queuePoll = setInterval(async () => {
    try {
      const st = await fetch("/api/submit/status").then(r => r.json());
      if ((st.pending || []).some(j => j.run_uuid === uuid)) return;
      clearInterval(_queueTicker); clearInterval(_queuePoll);
      $("btn-cancel-submit").style.display = "none";
      const lr = st.last;
      if (lr && lr.run_uuid === uuid && lr.ok) {
        $("btn-submit").textContent = "✓ 已自动提交";
        $("runarea-info").innerHTML =
          `<span style="color:var(--green)">定时提交完成：` +
          `rrid=${lr.rrid} uuid=${uuid} ｜OBS ${lr.obs_ok}/2` +
          `${lr.verified ? " ｜已回读" : ""}</span>`;
        refreshSessionFlags();
      } else {
        $("btn-submit").disabled = false;
        $("btn-submit").textContent = "提交记录";
      }
    } catch (_) {}
  }, 15000);
}
$("btn-cancel-submit").onclick = async () => {
  try { await api("/api/submit/cancel", {}); } catch (e) {
    $("runarea-info").innerHTML =
      `<span style="color:var(--red)">取消失败：${esc(e.message)}</span>`;
    return;
  }
  clearInterval(_queueTicker); clearInterval(_queuePoll);
  $("btn-cancel-submit").style.display = "none";
  $("btn-submit").disabled = false;
  $("btn-submit").textContent = "提交记录";
  $("runarea-info").innerHTML =
    `<span style="color:var(--warn)">已取消排队提交</span>`;
};

$("btn-submit").onclick = async () => {
  if (!lastFit || !lastFit.locs || !lastFit.locs.length) return;
  updateSubmitWarn();   // 点击时再复查一次，跨过阈值立即显示
  $("btn-submit").disabled = true;
  $("btn-submit").textContent = "排队中…";
  setRunBlock("busy");
  try {
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
      `<span style="color:var(--green)">` +
      `${r.already ? "该轨迹此前已提交过：" : "√ 提交成功 "}` +
      `rrid=${r.rrid} uuid=${r.uuid}${obsTxt}${verTxt}</span>`;
    refreshSessionFlags();   // 回执+锚点落盘后刷新自动跑解锁态
  } catch (e) {
    flashBtnResult($("btn-submit"), false, "提交记录");
    $("runarea-info").innerHTML =
      `<span style="color:var(--red)">提交失败：${esc(e.message)}</span>`;
  } finally {
    setRunBlock(sess ? "idle" : "off");
  }
};
