const $ = id => document.getElementById(id);
const statusEl = $("status"), statusText = $("status-text");
let map = null, sess = null, AMap_ = null;
let locMarker = null, picking = false;
let overlays = [];    // 阶段一图层：打卡点
let fitOverlays = []; // 阶段二图层：拟合跑道 + 轨迹 + 起终点
let trackLocs = [];   // 当前轨迹中线 BD [[lat,lon],...]（可被控制点编辑）
let origLocs = [];    // 拟合输出的原始中线（调整的基准）
let lapIdx = [];      // 每点所在圈号（显示层沿法线错开画）
let lapCount = 0;
let snapIdx = [];     // 打卡点吸附的轨迹下标 = 固定锚点
let trackSegs = [];   // 分圈轨迹的 polyline {pl,i0,i1}
let ctrls = [];       // {locIdx,anchor,pos:[lat,lon],marker} 可拖控制点
let ctrlTotal = 8;    // 控制点总数（滑块可调，上限 10）
let ctrlWeight = 0.8; // 点权重：0=无影响，1=路径必过该点
let ctrlRadius = 12;  // 影响半径（米）：窗外轨迹严格不受影响
let editing = false;
let lastFit = null;   // 最近一次拟合完整响应（summary/详细数据/提交用）
let FE = {            // /api/defaults 下发的配置（run/frontend 两组）
  run: {}, frontend: {},
};

function setStatus(cls, text) {
  statusEl.className = cls;
  statusText.textContent = text;
}

async function api(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
  return d;
}

/* ---------- BD-09 <-> GCJ-02 转换（前端本地算，与后端数据保持 BD-09） ---------- */
const X_PI = Math.PI * 3000.0 / 180.0;
function bd09ToGcj02(lat, lon) {
  const x = lon - 0.0065, y = lat - 0.006;
  const z = Math.sqrt(x * x + y * y) - 0.00002 * Math.sin(y * X_PI);
  const t = Math.atan2(y, x) - 0.000003 * Math.cos(x * X_PI);
  return [z * Math.sin(t), z * Math.cos(t)];   // [lat, lon]
}
function gcj02ToBd09(lat, lon) {
  const x = lon, y = lat;
  const z = Math.sqrt(x * x + y * y) + 0.00002 * Math.sin(y * X_PI);
  const t = Math.atan2(y, x) + 0.000003 * Math.cos(x * X_PI);
  return [z * Math.sin(t) + 0.006, z * Math.cos(t) + 0.0065];   // [lat, lon]
}

/* ---------- 定位输入框：始终 GCJ-02（地图原生），原样提交服务端 ---------- */

function readInputs() {
  const lat = parseFloat($("inp-lat").value);
  const lon = parseFloat($("inp-lon").value);
  if (!isFinite(lat) || !isFinite(lon)) return null;
  return [lat, lon];
}
function writeInputs(gLat, gLon) {
  $("inp-lat").value = gLat.toFixed(7);
  $("inp-lon").value = gLon.toFixed(7);
}

function bdToLngLat(bdLat, bdLon) {
  const [gLat, gLon] = bd09ToGcj02(bdLat, bdLon);
  return new AMap_.LngLat(gLon, gLat);
}

// 锚点地标：跟随输入框坐标移动，已存在则平滑移动不重建（GCJ 直接上图）
function setMarkerAt(gLat, gLon, label) {
  const ll = new AMap_.LngLat(gLon, gLat);
  if (locMarker) {
    locMarker.setPosition(ll);
    if (label) locMarker.setLabel({ content: label, direction: "top" });
  } else {
    locMarker = new AMap_.Marker({
      position: ll, map, anchor: "bottom-center",
      label: { content: label || "锚点", direction: "top" },
    });
  }
  return ll;
}

// 输入框变化 -> 地标实时跟随（不改地图视野）
function syncMarkerFromInputs() {
  const c = readInputs();
  if (c && map) setMarkerAt(c[0], c[1]);
}

/* ---------- 高德地图 ---------- */

// 高德 loader：拿到 /api/defaults 的 key/security 后才注入 script
let _loaderP = null;
function ensureLoader() {
  if (!_loaderP) {
    window._AMapSecurityConfig = { securityJsCode: FE.frontend.amap_security };
    _loaderP = new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = "https://webapi.amap.com/loader.js";
      s.onload = res;
      s.onerror = () => rej(new Error("高德 loader 加载失败"));
      document.head.appendChild(s);
    });
  }
  return _loaderP;
}

function initMap() {
  return ensureLoader().then(() => AMapLoader.load({
    key: FE.frontend.amap_key,
    version: "2.0",
    plugins: ["AMap.Geolocation", "AMap.Scale", "AMap.ToolBar", "AMap.ConvertFrom"],
  })).then(AMap => {
    AMap_ = AMap;
    map = new AMap.Map("map", {
      zoom: 15, dragEnable: true, zoomEnable: true,
    });
    map.addControl(new AMap.Scale());
    map.addControl(new AMap.ToolBar({ position: "RB" }));
    map.on("click", onMapClick);
    // 初始视图：按输入框锚点
    const c = readInputs();
    if (c) map.setZoomAndCenter(16, setMarkerAt(c[0], c[1]));
    locateMe(AMap);
  });
}

function onMapClick(e) {
  if (!picking) return;
  const gLat = e.lnglat.getLat(), gLon = e.lnglat.getLng();
  writeInputs(gLat, gLon);   // GCJ-02 原样显示并提交
  map.setCenter(setMarkerAt(gLat, gLon, "选点"));
  togglePick(false);
}

// force=false 时若输入框已有锚点（配置回填）则不覆盖；按钮手动定位传 force=true
function locateMe(AMap, force) {
  const fill = (gLat, gLon, label) => {
    if (!force && readInputs()) return;
    writeInputs(gLat, gLon);
    map.setCenter(setMarkerAt(gLat, gLon, label));
  };
  const geo = new AMap.Geolocation({
    enableHighAccuracy: true, timeout: 10000,
    convert: true, showButton: false, showMarker: false,
  });
  geo.getCurrentPosition((status, result) => {
    if (status === "complete") {
      fill(result.position.lat, result.position.lng, "本机定位");
    } else {
      navigator.geolocation?.getCurrentPosition(
        pos => {
          const ll = [pos.coords.longitude, pos.coords.latitude];
          AMap.convertFrom(ll, "gps", (st, res) => {
            const g = (st === "ok" && res.locations[0]) ? res.locations[0] : ll;
            fill(g.lat, g.lng, "GPS定位");
          });
        },
        () => {},
        { enableHighAccuracy: true, timeout: 8000 },
      );
    }
  });
}

/* ---------- 面板按钮 ---------- */

function togglePick(force) {
  picking = (force !== undefined) ? force : !picking;
  $("btn-pick").classList.toggle("active", picking);
  $("btn-pick").textContent = picking ? "点击地图选点…" : "地图选点";
  $("map").classList.toggle("picking", picking);
}

// 登录/恢复会话后应用后端保存的锚点；无记录返回 false -> 首开本机定位
function applyAnchor(a) {
  if (a && isFinite(+a.lat) && isFinite(+a.lon)) {
    writeInputs(+a.lat, +a.lon);
    if (map) map.setCenter(setMarkerAt(+a.lat, +a.lon, "上次锚点"));
    return true;
  }
  return false;
}

$("btn-pick").onclick = () => togglePick();
$("btn-geoloc").onclick = () => { if (AMap_) locateMe(AMap_, true); };
$("btn-goto").onclick = () => {
  const c = readInputs();
  if (!c) return;
  map.setZoomAndCenter(Math.max(map.getZoom(), 16), setMarkerAt(c[0], c[1], "锚点"));
};

// 经纬度输入框实时驱动地标
$("inp-lat").addEventListener("input", syncMarkerFromInputs);
$("inp-lon").addEventListener("input", syncMarkerFromInputs);

/* ---------- 跑步配置：伪装时间 / 距离 / 均速↔用时联动 ---------- */

// 以下均为内置兜底值，启动时被 /api/defaults 下发的 projectConfig 覆盖
let CFG_DEFAULT_DIST = 2200, CFG_DEFAULT_DUR = 720;
let CFG_START_LO = 6, CFG_START_HI = 22;   // 伪装时间许可窗口 06:00–22:00
let CFG_DEF_START = "15:30";               // 默认起跑时刻
let CFG_JITTER = [1, 5];                   // 默认起跑随机偏移范围（分钟）
let TAPER_PTS = 14;                        // 打卡锚点渐隐范围（密点数）
let SAMPLE_ROWS = 40;                      // 逐点采样表目标行数

function cfgDist() {
  const v = parseFloat($("cfg-dist").value);
  return isFinite(v) && v > 0 ? v : CFG_DEFAULT_DIST;
}
function cfgSpeed() {
  const v = parseFloat($("cfg-speed").value);
  return isFinite(v) && v > 0 ? v : null;
}
// 全角冒号/数字归一成半角（"１５：００" -> "15:00"）
function halfWidth(s) {
  return s.replace(/[：∶]/g, ":")
          .replace(/[０-９]/g, c => String.fromCharCode(c.charCodeAt(0) - 0xFEE0));
}

function cfgDur() {
  const raw = halfWidth(($("cfg-dur").value || "").trim());
  const m = raw.match(/^(?:(\d+):)?([0-5]?\d):([0-5]\d)$/) || raw.match(/^(\d+):([0-5]?\d)$/);
  if (m) return m.length === 4 ? (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3])
                              : (+m[1]) * 60 + (+m[2]);
  const v = parseFloat(raw);
  return isFinite(v) && v > 0 ? Math.round(v) : null;
}
// 有效用时：显式用时 > 均速推导 > 默认 720s
function cfgEffDur() {
  const d = cfgDur(), sp = cfgSpeed();
  return d || (sp ? Math.round(cfgDist() / sp) : CFG_DEFAULT_DUR);
}

// 联动：改距离/均速 -> 重算用时；改用时 -> 重算均速
$("cfg-dist").addEventListener("input", () => {
  const sp = cfgSpeed();
  if (sp) $("cfg-dur").value = fmtDur(cfgDist() / sp);
  updateCfgHint();
});
$("cfg-speed").addEventListener("input", () => {
  const sp = cfgSpeed();
  if (sp) $("cfg-dur").value = fmtDur(cfgDist() / sp);
  updateCfgHint();
});
$("cfg-dur").addEventListener("input", () => {
  const d = cfgDur();
  if (d) $("cfg-speed").value = (cfgDist() / d).toFixed(2);
  updateCfgHint();
});
$("cfg-start").addEventListener("input", () => {
  startCache = null;   // 输入变了才重抽随机偏移
  updateCfgHint();
});
$("cfg-date").addEventListener("input", () => {
  startCache = null;
  updateCfgHint();
});

// 伪装起跑：日期(可空=今天) + HH:MM(窗口 06:00–22:00，否则默认 15:30±[1,5]min)。
// 所选起跑落在未来 -> 黄字警告并把日期自动调回当天（当天仍未发生则退昨天）。
let startCache = null;   // {ms, warn}
function resolveStart() {
  if (!startCache) startCache = computeStart();
  return startCache;
}
function resolveStartMs() { return resolveStart().ms; }

function computeStart() {
  const raw = halfWidth(($("cfg-start").value || "").trim());
  const durMs = cfgEffDur() * 1000 + 60000;
  const dv = ($("cfg-date").value || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  const m = raw.match(/^([01]?\d|2[0-3]):([0-5]\d)$/);
  const inWin = m && +m[1] >= CFG_START_LO && +m[1] < CFG_START_HI;
  const [dh, dm] = CFG_DEF_START.split(":").map(Number);
  const jitter = (CFG_JITTER[0] +
    Math.floor(Math.random() * (CFG_JITTER[1] - CFG_JITTER[0] + 1))) *
    (Math.random() < .5 ? -1 : 1);
  const sec = Math.floor(Math.random() * 60);
  const applyTime = d => inWin
    ? d.setHours(+m[1], +m[2], sec, 0)
    : d.setHours(dh, dm + jitter, sec, 0);
  const p = n => String(n).padStart(2, "0");

  let base = dv ? new Date(+dv[1], +dv[2] - 1, +dv[3]) : new Date();
  applyTime(base);
  let ms = base.getTime(), warn = null;
  if (ms + durMs > Date.now()) {              // 起跑在未来 -> 日期调回当天
    warn = "所选起跑时间尚未发生，已自动调整为当天日期";
    const now = new Date();
    $("cfg-date").value =
      `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
    base = new Date();
    applyTime(base);
    ms = base.getTime();
    if (ms + durMs > Date.now()) ms -= 86400000;  // 当天该时刻仍未发生 -> 昨天
  }
  return { ms, warn };
}

function updateCfgHint() {
  const { ms, warn } = resolveStart(), d = new Date(ms);
  const p = n => String(n).padStart(2, "0");
  $("cfg-hint").innerHTML =
    (warn ? `<span style="color:#b7791f">⚠ ${warn}</span><br>` : "") +
    `起跑：${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}` +
    ` ｜ ${cfgDist().toFixed(0)}m · ${fmtDur(cfgEffDur())} · ` +
    `${(cfgDist() / cfgEffDur()).toFixed(2)} m/s`;
}
function fitGoal() {
  return { dist: cfgDist(), dur: cfgEffDur(), start_ms: resolveStartMs() };
}

/* ---------- 跑步区域：① 拉点 -> ② 拟合 ---------- */

function clearOverlays() {
  overlays.concat(fitOverlays).forEach(o => o.setMap && o.setMap(null));
  overlays = []; fitOverlays = [];
}

// 打卡点红点 + 名称上图（供 拉点/自动重拉 复用）
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

// 阶段一：按锚点拉打卡点，画红点
async function fetchPoints() {
  if (!sess) { setStatus("err", "请先登录"); return; }
  const c = readInputs();
  if (!c) { $("runarea-info").textContent = "经纬度无效"; return; }
  $("btn-points").disabled = true;
  $("btn-fit").disabled = true;
  $("runarea-info").textContent = "拉取打卡点中…";
  try {
    const d = await api("/api/points", { lat: c[0], lon: c[1] });
    clearOverlays();
    clearCtrls();
    drawPoints(d);
    map.setFitView(overlays);
    // 新点位让旧轨迹作废：解锁拟合/调整入口，收回提交与编辑态
    trackLocs = []; origLocs = []; lapIdx = []; snapIdx = [];
    lastFit = null;
    $("submit-card").style.display = "none";
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
  }
}

let MAX_REFIT = 3;     // 无可行解时自动 重拉点->重拟合 的最大次数（受点位限流约束）

// 拟合跑道虚线 + 蓝轨迹 + 起终点上图
function renderFit(d, retried) {
  if (!d || !Array.isArray(d.locs) || !d.locs.length ||
      !Array.isArray(d.track_bd)) {
    console.error("fit resp:", d);
    fitError("拟合返回数据异常（轨迹点为空或格式不对），F12 控制台有原始响应");
    return;
  }
  clearCtrls();
  // 兼容数组行 [lat,lon,lap] 与字典行 {gLat,gLng,...}；不过滤，保持下标与 snapIdx 对齐
  const row = p => Array.isArray(p)
    ? [p[0], p[1]] : [p.gLat ?? p.lat, p.gLng ?? p.lon];
  trackLocs = d.locs.map(row);
  origLocs = trackLocs.map(p => p.slice());   // 原始中线：手动调整的基准
  lapIdx = d.locs.map(p => (Array.isArray(p) ? p[2] : 0) || 0);
  lapCount = lapIdx.length ? Math.max(...lapIdx) + 1 : 1;
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
  // 详细数据：完整展示本次生成的所有字段（含 locs_full 逐点记录）
  $("fit-detail-json").textContent = JSON.stringify(d, null, 1);
  $("submit-card").style.display = "";
  $("btn-submit").disabled = !d.locs.length;
  $("runarea-info").innerHTML =
    (retried ? `<span style="color:#b7791f">重新拉点后拟合成功：</span><br>` : "") +
    `直道 ${f.straight.toFixed(0)}m + 半圆 r=${f.radius.toFixed(0)}m | 周长 ${f.perimeter.toFixed(0)}m | 轨迹 ${d.locs.length} 点<br>` +
    `误差 均=${f.mean_err.toFixed(1)}m 最大=${f.max_err.toFixed(1)}m | 边界余量 ${f.fence_margin.toFixed(1)}m | 锚点 ${snapIdx.length} 个`;
  if (editing) buildControls();
}

function fitError(msg) {
  $("runarea-info").innerHTML = `<span style="color:#e53e3e">${msg}</span>`;
}

/* ---------- 本次拟合信息卡 ---------- */

function fmtDur(s) {
  s = Math.round(s);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:` +
         `${String(s % 60).padStart(2, "0")}`;
}
function fmtPace(sPerKm) {
  if (sPerKm == null) return "--";
  const m = Math.floor(sPerKm / 60), sec = Math.round(sPerKm % 60);
  return `${m}'${String(sec).padStart(2, "0")}"`;
}

// 详细数据区：诊断表格 + 分圈统计 + 速度曲线 + 逐点采样表
function renderDetail(d) {
  const f = d.fit || {}, full = d.locs_full || [];
  const snap = new Set(d.snap_idx || []);
  const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const tbl = (heads, rows) =>
    `<table><tr>${heads.map(h => `<th>${h}</th>`).join("")}</tr>` +
    rows.map(r => `<tr${r.snap ? ' class="snap"' : ""}>` +
      r.cells.map(c => `<td>${c}</td>`).join("") + `</tr>`).join("") +
    `</table>`;
  let html = "";

  // ① 拟合诊断
  if (Object.keys(f).length) {
    html += `<div class="detail-sec"><h4>跑道拟合诊断</h4>` + tbl(
      ["直道m", "半径m", "周长m", "均误差m", "最大误差m", "边界余量m", "收敛", "备选解", "中心角°"],
      [{ cells: [
        f.straight?.toFixed(1) ?? "-", f.radius?.toFixed(1) ?? "-",
        f.perimeter?.toFixed(1) ?? "-", f.mean_err?.toFixed(2) ?? "-",
        f.max_err?.toFixed(2) ?? "-", f.fence_margin?.toFixed(1) ?? "-",
        f.converged ? "✓" : "✗", f.alt_solutions ?? "-",
        f.angle_deg?.toFixed(1) ?? "-",
      ]}]) + `</div>`;
  }

  // ② 分圈统计（locs 第3列是圈号，与 locs_full 同序）
  if (full.length && Array.isArray(d.locs)) {
    const laps = {};
    d.locs.forEach((p, i) => {
      const lap = (Array.isArray(p) ? p[2] : 0) || 0;
      (laps[lap] = laps[lap] || []).push(full[i] || {});
    });
    let prevD = 0, prevT = 0;
    const rows = Object.keys(laps).sort((a, b) => a - b).map(k => {
      const pts = laps[k], last = pts[pts.length - 1] || {};
      const dis = (last.totalDis || 0) - prevD, t = (last.totalTime || 0) - prevT;
      prevD = last.totalDis || 0; prevT = last.totalTime || 0;
      return { cells: [
        `第${+k + 1}圈`, dis.toFixed(0), fmtDur(t),
        fmtPace(dis > 0 ? t / (dis / 1000) : null), pts.length,
      ] };
    });
    html += `<div class="detail-sec"><h4>分圈统计</h4>` +
      tbl(["圈", "里程m", "用时", "配速", "点数"], rows) + `</div>`;
  }

  // ③ 速度曲线（canvas）
  html += `<div class="detail-sec"><h4>速度曲线（红点=打卡吸附点，灰线=圈界）</h4>` +
    `<canvas id="speed-chart"></canvas></div>`;

  // ④ 逐点采样表（等距 ~40 行 + 全部吸附点）
  if (full.length) {
    const step = Math.max(1, Math.floor(full.length / SAMPLE_ROWS));
    const rows = [];
    for (let i = 0; i < full.length; i++) {
      if (i % step !== 0 && !snap.has(i) && i !== full.length - 1) continue;
      const q = full[i];
      rows.push({ snap: snap.has(i), cells: [
        i, fmtDur(q.totalTime || 0), (q.totalDis || 0).toFixed(1),
        q.steps ?? "-",
        (q.speed ?? "-") === "-" ? "-" : (+q.speed / 3.6).toFixed(2),
        (q.bdA ?? "-") === "-" ? "-" : (+q.bdA).toFixed(1),
        q.type ?? q.ptype ?? "-", snap.has(i) ? "●" : "",
      ] });
    }
    html += `<div class="detail-sec"><h4>逐点采样（每 ${step} 点 + 全部打卡点）</h4>` +
      `<div class="detail-scroll">` +
      tbl(["#", "t", "里程m", "步数", "速度m/s", "海拔m", "类型", "打卡"], rows) +
      `</div></div>`;
  }

  // ⑤ 分段统计（60s 窗口，协议 segments 字段）
  if (Array.isArray(d.segments) && d.segments.length) {
    const rows = d.segments.map((s, i) => ({ cells: [
      i + 1, fmtDur(s.totalTime || 0), s.distance ?? "-",
      (s.avgSpeed ?? 0).toFixed(2), s.avgStep ?? "-",
    ] }));
    html += `<div class="detail-sec"><h4>分段统计（60s/段）</h4>` +
      `<div class="detail-scroll">` +
      tbl(["段", "时长", "里程m", "均速m/s", "步频spm"], rows) +
      `</div></div>`;
  }

  // ⑥ 10s 窗（提交形态：id=60000+i、queueNum=i）
  if (Array.isArray(d.speed_windows) && d.speed_windows.length) {
    const t0 = d.summary?.start_ms || 0;
    const rows = d.speed_windows.map((w, i) => {
      const st = d.step_windows?.[i] || {};
      return { cells: [
        i, fmtDur((w.endTime - t0) / 1000), (w.distance || 0).toFixed(1),
        st.stepsNum ?? "-", w.id, w.queueNum,
      ] };
    });
    html += `<div class="detail-sec"><h4>10 秒窗统计（提交形态）</h4>` +
      `<div class="detail-scroll">` +
      tbl(["#", "窗末t", "里程m", "步数", "id", "queueNum"], rows) +
      `</div></div>`;
  }

  // ⑦ 公里圈（OBS laps_json 形态：每 1000m 一圈）
  if (Array.isArray(d.laps) && d.laps.length) {
    const rows = d.laps.map(l => ({ cells: [
      l.lapIndex, (l.distance || 0).toFixed(0), fmtDur(l.duration || 0),
      fmtPace(l.avgPace), l.step ?? "-",
      (l.avgStride ?? "-") === "-" ? "-" : (+l.avgStride).toFixed(0),
      (l.avgCadence ?? "-") === "-" ? "-" : (+l.avgCadence).toFixed(0),
      (l.elevationGain ?? 0).toFixed(1), l.isFullLap ? "✓" : "残",
    ] }));
    html += `<div class="detail-sec"><h4>公里圈（OBS laps_json）</h4>` +
      tbl(["圈", "里程m", "用时", "配速", "步数", "步幅cm", "步频", "爬升m", "整圈"], rows) +
      `</div>`;
  }

  // 服务端下发的 5 个打卡点坐标
  if (Array.isArray(f.point_errors) && f.point_errors.length) {
    html += `<div class="detail-sec"><h4>各打卡点拟合误差（m）</h4>` +
      tbl(["#1", "#2", "#3", "#4", "#5", "#6"].slice(0, f.point_errors.length),
        [{ cells: f.point_errors.map(e => (+e).toFixed(2)) }]) + `</div>`;
  }
  $("fit-detail-view").innerHTML = html;
  drawSpeedChart(full, snap, d.locs);
}

// 速度曲线：横轴=轨迹点序，纵轴=瞬时速度；灰竖线=圈界，红点=打卡点
function drawSpeedChart(full, snap, locs) {
  const cv = $("speed-chart");
  if (!cv || !full.length) return;
  const W = cv.clientWidth || 300, H = 120, pad = 6;
  const dpr = window.devicePixelRatio || 1;
  cv.width = W * dpr; cv.height = H * dpr;
  const ctx = cv.getContext("2d");
  ctx.scale(dpr, dpr);
  const speeds = full.map(q => (+q.speed || 0) / 3.6);   // speed 字段是 km/h，转 m/s
  const vmax = Math.max(...speeds, 0.1), vmin = Math.min(...speeds);
  const X = i => pad + i * (W - 2 * pad) / (full.length - 1);
  const Y = v => H - pad - (v - vmin) * (H - 2 * pad) / (vmax - vmin || 1);
  // 圈界竖线
  ctx.strokeStyle = "#cbd5e0"; ctx.setLineDash([3, 3]);
  let prevLap = 0;
  (locs || []).forEach((p, i) => {
    const lap = (Array.isArray(p) ? p[2] : 0) || 0;
    if (lap !== prevLap) {
      ctx.beginPath(); ctx.moveTo(X(i), pad); ctx.lineTo(X(i), H - pad); ctx.stroke();
      prevLap = lap;
    }
  });
  ctx.setLineDash([]);
  // 海拔折线（bdA，独立纵轴缩放到同幅面，橙色细线）
  const alts = full.map(q => +q.bdA || 0);
  const amax = Math.max(...alts, 0.1), amin = Math.min(...alts);
  const YA = v => H - pad - (v - amin) * (H - 2 * pad) / (amax - amin || 1);
  ctx.strokeStyle = "#dd6b20"; ctx.lineWidth = 0.8; ctx.globalAlpha = 0.7;
  ctx.beginPath();
  alts.forEach((v, i) => i ? ctx.lineTo(X(i), YA(v)) : ctx.moveTo(X(i), YA(v)));
  ctx.stroke();
  ctx.globalAlpha = 1;
  // 速度折线
  ctx.strokeStyle = "#3182ce"; ctx.lineWidth = 1.2; ctx.beginPath();
  speeds.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v)));
  ctx.stroke();
  // 打卡点标记
  ctx.fillStyle = "#e53e3e";
  snap.forEach(i => {
    if (i < speeds.length) { ctx.beginPath(); ctx.arc(X(i), Y(speeds[i]), 3, 0, 7); ctx.fill(); }
  });
  // 纵轴范围标注
  ctx.fillStyle = "#a0aec0"; ctx.font = "10px sans-serif";
  ctx.fillText(vmax.toFixed(2) + " m/s", pad + 2, pad + 9);
  ctx.fillText(vmin.toFixed(2), pad + 2, H - pad - 2);
  ctx.fillStyle = "#dd6b20";
  ctx.fillText(amin.toFixed(1) + "~" + amax.toFixed(1) + " m", W - pad - 68, pad + 9);
}

// 信息卡汇总行；adjustedMeters 非空表示手动调整后的估算里程
function renderSummary(sum, adjustedMeters) {
  if (!sum) return;
  const dist = adjustedMeters != null ? adjustedMeters : sum.distance_m;
  const adj = adjustedMeters != null ? "~" : "";
  let startChip = "";
  if (sum.start_ms) {
    const d = new Date(sum.start_ms);
    const p = n => String(n).padStart(2, "0");
    startChip = `<div class="kv">起跑<b>${p(d.getHours())}:${p(d.getMinutes())}</b></div>`;
  }
  $("fit-summary").innerHTML =
    `<div class="kv">距离<b>${adj}${(dist / 1000).toFixed(2)} km</b></div>` +
    `<div class="kv">用时<b>${fmtDur(sum.duration_s)}</b></div>` +
    `<div class="kv">平均配速<b>${fmtPace(sum.pace_s_per_km)}/km</b></div>` +
    `<div class="kv">均速<b>${sum.avg_speed_mps} m/s</b></div>` +
    `<div class="kv">总步数<b>${sum.total_steps ?? "-"}</b></div>` +
    `<div class="kv">平均步频<b>${sum.step_freq ?? "-"} spm</b></div>` +
    `<div class="kv">卡路里<b>${sum.calorie ?? "-"} kcal</b></div>` +
    `<div class="kv">平均功率<b>${sum.avg_power ?? "-"} W</b></div>` +
    `<div class="kv">累计爬升<b>${sum.ascent_m ?? "-"} m</b></div>` +
    `<div class="kv">有效里程<b>${sum.valid_dis_m != null ? (sum.valid_dis_m / 1000).toFixed(2) + " km" : "-"}</b></div>` +
    `<div class="kv">轨迹点<b>${sum.point_count}</b></div>` +
    `<div class="kv">圈数<b>${sum.lap_count}</b></div>` +
    `<div class="kv">打卡锚点<b>${sum.snapped}</b></div>` + startChip;
}

/* ---------- 轨迹手动调整：控制点=次级打卡点 ----------
   拖一个控制点 = 给该位置加一个"软必过点"：
   空间上附近的所有轨迹点（各圈重叠处一起）按余弦窗位移，
   半径外严格归零 -> 绕行结束后轨迹立刻回到 1 道；
   中心权重=ctrlWeight（< 真打卡点的 1.0），打卡锚点附近渐隐到 0。 */

function metersBetween(a, b) {
  const fLng = MET_L * Math.cos(a[0] * Math.PI / 180);
  return Math.hypot((b[0] - a[0]) * MET_L, (b[1] - a[1]) * fLng);
}

// 由全部控制点重算中线：trackLocs = origLocs + Σ 把手位移×余弦窗权重
function recomputeTrack() {
  const n = trackLocs.length;
  for (let i = 0; i < n; i++) {
    let dy = 0, dx = 0;
    const tw = taperWeight(i);
    if (tw > 0) {
      for (const c of ctrls) {
        const d = metersBetween(origLocs[i], c.anchor);
        if (d >= ctrlRadius) continue;   // 窗外严格无影响：绕行有硬边界
        const w = ctrlWeight * 0.5 *
                  (1 + Math.cos(Math.PI * d / ctrlRadius)) * tw;
        dy += (c.pos[0] - c.anchor[0]) * w;
        dx += (c.pos[1] - c.anchor[1]) * w;
      }
    }
    trackLocs[i] = [origLocs[i][0] + dy, origLocs[i][1] + dx];
  }
  redrawTrack();
}

// 分圈显示：中线数据不变，画的时候按圈号沿法线错开（显示层偏移）
let LAP_COLORS = ["#38b2ac", "#3182ce", "#805ad5",
                  "#dd6b20", "#e53e3e", "#d69e2e"];
let LANE_W = 2.8;       // 圈间距（米）
const MET_L = 111195;

// 打卡点附近偏移渐隐到 0：轨迹仍精确穿过 5 个锚点
function taperWeight(i) {
  const n = trackLocs.length;
  let dmin = n;
  for (const j of snapIdx) {
    const d = Math.min(Math.abs(i - j), n - Math.abs(i - j));
    if (d < dmin) dmin = d;
  }
  return Math.min(1, dmin / TAPER_PTS);
}

// 轨迹点 i 的显示坐标 = 中线 + 法线方向 × (圈偏移 × 渐隐权重)
function dispCoord(i) {
  const n = trackLocs.length;
  const a = trackLocs[(i - 1 + n) % n], b = trackLocs[(i + 1) % n];
  const fLng = MET_L * Math.cos(trackLocs[i][0] * Math.PI / 180);
  const tx = (b[1] - a[1]) * fLng, ty = (b[0] - a[0]) * MET_L;
  const tl = Math.hypot(tx, ty) || 1;
  const lane = ((lapIdx[i] || 0) - (lapCount - 1) / 2) * LANE_W * taperWeight(i);
  return [trackLocs[i][0] + (-ty / tl) * lane / MET_L,
          trackLocs[i][1] + (tx / tl) * lane / fLng];
}

// 按圈号切连续段，每圈一条 polyline 一个颜色
function drawTrackLaps() {
  trackSegs = [];
  const n = trackLocs.length;
  let i = 0;
  while (i < n - 1) {
    const lap = lapIdx[i] || 0;
    let j = i;
    while (j < n - 1 && (lapIdx[j + 1] || 0) === lap) j++;
    const end = Math.min(j + 1, n - 1);   // 最后一段封到 n-1，防越界
    const path = [];
    for (let k = i; k <= end; k++) path.push(bdToLngLat(...dispCoord(k)));
    const pl = new AMap_.Polyline({
      path, map, zIndex: 40 + lap, strokeWeight: 2.5, strokeOpacity: 0.9,
      strokeColor: LAP_COLORS[lap % LAP_COLORS.length],
      lineJoin: "round", lineCap: "round",
    });
    trackSegs.push({ pl, i0: i, i1: j + 1 });
    fitOverlays.push(pl);
    i = j + 1;
  }
}
function redrawTrack() {
  trackSegs.forEach(s => {
    const end = Math.min(s.i1, trackLocs.length - 1);
    const path = [];
    for (let k = s.i0; k <= end; k++)
      path.push(bdToLngLat(...dispCoord(k)));
    s.pl.setPath(path);
  });
}

// 把手圆点：染成所在圈的圈色
function ctrlHtml(locIdx) {
  const color = LAP_COLORS[(lapIdx[locIdx] || 0) % LAP_COLORS.length];
  return `<div style="width:12px;height:12px;border-radius:50%;` +
    `background:${color};border:2px solid #fff;` +
    `box-shadow:0 0 4px rgba(0,0,0,.5)"></div>`;
}

// 把手落回显示线上：位置 = 锚定密点的偏移后显示坐标
function updateCtrlMarkers() {
  ctrls.forEach(c => {
    c.marker.setPosition(bdToLngLat(...dispCoord(c.locIdx)));
    c.marker.setContent(ctrlHtml(c.locIdx));
  });
}

// 沿轨迹均匀布 ctrlTotal 个控制点
function buildControls() {
  clearCtrls();
  const n = trackLocs.length;
  if (n < 10) return;
  const step = Math.max(1, Math.round(n / ctrlTotal));
  for (let locIdx = Math.floor(step / 2); locIdx < n; locIdx += step) {
    const c = { locIdx,
                anchor: origLocs[locIdx].slice(),
                pos: origLocs[locIdx].slice() };
    c.marker = new AMap_.Marker({
      position: bdToLngLat(...dispCoord(locIdx)), map, zIndex: 85,
      draggable: true, cursor: "move",
      content: ctrlHtml(locIdx), offset: new AMap_.Pixel(-6, -6),
    });
    c.marker.on("dragend", e => {
      const g = e.lnglat;
      c.pos = gcj02ToBd09(g.getLat(), g.getLng());
      recomputeTrack();       // 附近所有轨迹（各圈）一起被拉扯
      updateCtrlMarkers();    // 把手吸附回变形后的显示线
      updateTrackInfo();
    });
    ctrls.push(c);
  }
}

function clearCtrls() {
  ctrls.forEach(c => c.marker && c.marker.setMap(null));
  ctrls = [];
}

// 编辑后重新估算轨迹总长（BD 经纬度 -> 米）
function trackMeters() {
  const fLat = 111195, fLng = 111195 * Math.cos(22.9 * Math.PI / 180);
  let s = 0;
  for (let i = 1; i < trackLocs.length; i++) {
    const a = trackLocs[i - 1], b = trackLocs[i];
    s += Math.hypot((b[0] - a[0]) * fLat, (b[1] - a[1]) * fLng);
  }
  return s;
}

function updateTrackInfo() {
  const m = trackMeters();
  if (lastFit) renderSummary(lastFit.summary, m);   // 信息卡距离同步为调整后估算
  $("runarea-info").innerHTML =
    `<span style="color:#b7791f">轨迹已手动调整</span><br>` +
    `调整后里程约 ${m.toFixed(0)}m | 锚点 ${snapIdx.length} 个保持必过<br>` +
    `注意：调整只改形状，未重算时间/配速`;
}

$("ctrl-count").oninput = e => {
  ctrlTotal = +e.target.value;
  $("ctrl-count-val").textContent = e.target.value;
  if (editing) buildControls();
};

$("ctrl-weight").oninput = e => {
  ctrlWeight = +e.target.value;
  $("ctrl-weight-val").textContent = (+e.target.value).toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  if (editing) { recomputeTrack(); updateCtrlMarkers(); updateTrackInfo(); }
};

$("ctrl-radius").oninput = e => {
  ctrlRadius = +e.target.value;
  $("ctrl-radius-val").textContent = e.target.value + "m";
  if (editing) { recomputeTrack(); updateCtrlMarkers(); updateTrackInfo(); }
};

$("btn-edit").onclick = () => {
  if (!trackLocs.length) {
    $("runarea-info").innerHTML =
      `<span style="color:#e53e3e">请先「拟合轨迹」生成轨迹后再调整。</span>`;
    return;
  }
  editing = !editing;
  $("btn-edit").classList.toggle("active", editing);
  $("btn-edit").textContent = editing ? "完成调整" : "调整轨迹";
  $("ctrl-row").style.display = editing ? "" : "none";
  $("ctrl-wrow").style.display = editing ? "" : "none";
  $("ctrl-rrow").style.display = editing ? "" : "none";
  if (editing) {
    buildControls();
    $("runarea-info").innerHTML =
      `<span style="color:#e53e3e">⚠ 实验性功能：手动调整尚在调试，` +
      `效果可能与预期不符，必要时需重新拟合轨迹，请谨慎使用。</span><br>` +
      `<span style="color:#b7791f">拖动彩色圆点调整轨迹；` +
      `红色打卡点（${snapIdx.length} 个锚点）保持必过。</span>`;
  } else {
    clearCtrls();
    updateTrackInfo();
  }
};

// 拟合无可行解 -> 自动重拉打卡点 -> 重拟合（直到成功或拉点失败）
async function refetchAndRefit(prevErr, attempt) {
  $("btn-points").disabled = true;
  try {
    $("runarea-info").innerHTML =
      `<span style="color:#b7791f">拟合无可行解（${prevErr}）<br>` +
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
    $("submit-card").style.display = "none";
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

// 阶段二：拟合体育场 + 生成轨迹；无可行解时自动重拉点重试
async function doFit() {
  $("btn-fit").disabled = true;
  $("runarea-info").textContent = "拟合体育场跑道并生成轨迹中…";
  try {
    renderFit(await api("/api/fit", fitGoal()));
  } catch (e) {
    console.error(e);
    if (e.message.includes("无可行解")) await refetchAndRefit(e.message, 1);
    else fitError(e.message);
  } finally {
    $("btn-fit").disabled = false;
  }
}

$("btn-points").onclick = fetchPoints;
$("btn-fit").onclick = doFit;
$("btn-submit").onclick = () => {
  $("runarea-info").innerHTML =
    `<span style="color:#b7791f">提交功能尚未实现：跑步记录提交协议` +
    `（轨迹字段/签名/OBS 上传）仍在逆向中。</span>`;
};

/* ---------- 登录流程 ---------- */

async function doLogin() {
  const username = $("inp-user").value.trim();
  const password = $("inp-pass").value;
  if (!username || !password) { setStatus("err", "请输入账号密码"); return; }
  $("btn-login").disabled = true;
  setStatus("busy", "登录中...");
  try {
    sess = await api("/api/login", { username, password });
    setStatus("on", `已登录 ${sess.name || ""} (uid=${sess.uid})`);
    $("btn-logout").style.display = "";
    if (!map) await initMap().catch(e => setStatus("err", "地图加载失败: " + e));
    // 有该账号的历史锚点则回填；否则本机定位已在 initMap 中执行
    if (!applyAnchor(sess.anchor) && map) locateMe(AMap_);
  } catch (e) {
    setStatus("err", e.message);
  } finally {
    $("btn-login").disabled = false;
  }
}

async function doLogout() {
  await api("/api/logout").catch(() => {});
  sess = null;
  $("btn-logout").style.display = "none";
  setStatus("off", "未登录");
}

$("btn-login").onclick = doLogin;
$("btn-logout").onclick = doLogout;
$("inp-pass").addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });

/* ---------- 启动：拉配置 -> 恢复会话 ---------- */

(async function boot() {
  // ① /api/defaults：凭据预填 + projectConfig 的 run/frontend 覆盖内置默认
  try {
    const d = await fetch("/api/defaults").then(r => r.json());
    FE = d;
    if (d.username) $("inp-user").value = d.username;
    if (d.password) $("inp-pass").value = d.password;
    const R = d.run || {}, F = d.frontend || {}, C = F.ctrl || {};
    CFG_DEFAULT_DIST = R.default_dist ?? CFG_DEFAULT_DIST;
    CFG_DEFAULT_DUR = R.default_dur ?? CFG_DEFAULT_DUR;
    CFG_DEF_START = R.default_start || CFG_DEF_START;
    CFG_JITTER = R.start_jitter_min || CFG_JITTER;
    if (R.start_window) [CFG_START_LO, CFG_START_HI] = R.start_window;
    MAX_REFIT = F.max_refit ?? MAX_REFIT;
    LANE_W = F.lane_width ?? LANE_W;
    LAP_COLORS = F.lap_colors || LAP_COLORS;
    TAPER_PTS = F.taper_points ?? TAPER_PTS;
    SAMPLE_ROWS = F.sample_rows ?? SAMPLE_ROWS;
    ctrlTotal = C.total ?? ctrlTotal;
    ctrlWeight = C.weight ?? ctrlWeight;
    ctrlRadius = C.radius ?? ctrlRadius;
    $("cfg-dist").value = CFG_DEFAULT_DIST;
    $("cfg-dur").placeholder = fmtDur(CFG_DEFAULT_DUR) + "（默认）";
    $("ctrl-count").max = C.max || 10;
    $("ctrl-count").value = ctrlTotal;
    $("ctrl-count-val").textContent = ctrlTotal;
    $("ctrl-weight").value = ctrlWeight;
    $("ctrl-weight-val").textContent = ctrlWeight;
    $("ctrl-radius").value = ctrlRadius;
    $("ctrl-radius-val").textContent = ctrlRadius + "m";
  } catch (e) {
    console.warn("配置下发失败，使用内置默认值", e);
  }
  updateCfgHint();

  // ② 刷新恢复会话（含该账号保存的锚点）
  try {
    const d = await fetch("/api/session").then(r => r.json());
    if (d.logged_in) {
      sess = d;
      setStatus("on", `已登录 ${d.name || ""} (uid=${d.uid})`);
      $("btn-logout").style.display = "";
      await initMap().catch(() => {});
      if (!applyAnchor(d.anchor) && map) locateMe(AMap_);
    }
  } catch (e) { /* 忽略 */ }
})();
