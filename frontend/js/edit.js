/* ---------- 轨迹手动调整 ---------- */
let LAP_COLORS = ["#38b2ac", "#3182ce", "#805ad5",
                  "#dd6b20", "#e53e3e", "#d69e2e"];
const MET_L = 111195;

function metersBetween(a, b) {
  const fLng = MET_L * Math.cos(a[0] * Math.PI / 180);
  return Math.hypot((b[0] - a[0]) * MET_L, (b[1] - a[1]) * fLng);
}

function taperWeight(i) {
  const n = trackLocs.length;
  let dmin = n;
  for (const j of snapIdx) {
    const d = Math.min(Math.abs(i - j), n - Math.abs(i - j));
    if (d < dmin) dmin = d;
  }
  return Math.min(1, dmin / TAPER_PTS);
}

function recomputeTrack() {
  const n = trackLocs.length;
  for (let i = 0; i < n; i++) {
    let dy = 0, dx = 0;
    const tw = taperWeight(i);
    if (tw > 0) {
      for (const c of ctrls) {
        const d = metersBetween(origLocs[i], c.anchor);
        if (d >= ctrlRadius) continue;
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

function drawTrackLaps() {
  trackSegs = [];
  const n = trackLocs.length;
  let i = 0;
  while (i < n - 1) {
    const lap = lapIdx[i] || 0;
    let j = i;
    while (j < n - 1 && (lapIdx[j + 1] || 0) === lap) j++;
    const end = Math.min(j + 1, n - 1);
    const path = [];
    for (let k = i; k <= end; k++) path.push(bdToLngLat(...trackLocs[k]));
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
      path.push(bdToLngLat(...trackLocs[k]));
    s.pl.setPath(path);
  });
}

function ctrlHtml(locIdx) {
  const color = LAP_COLORS[(lapIdx[locIdx] || 0) % LAP_COLORS.length];
  return `<div style="width:12px;height:12px;border-radius:50%;` +
    `background:${color};border:2px solid #fff;` +
    `box-shadow:0 0 4px rgba(0,0,0,.5)"></div>`;
}

function updateCtrlMarkers() {
  ctrls.forEach(c => {
    c.marker.setPosition(bdToLngLat(...trackLocs[c.locIdx]));
    c.marker.setContent(ctrlHtml(c.locIdx));
  });
}

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
      position: bdToLngLat(...trackLocs[locIdx]), map, zIndex: 85,
      draggable: true, cursor: "move",
      content: ctrlHtml(locIdx), offset: new AMap_.Pixel(-6, -6),
    });
    c.marker.on("dragend", e => {
      const g = e.lnglat;
      c.pos = gcj02ToBd09(g.getLat(), g.getLng());
      recomputeTrack();
      updateCtrlMarkers();
      updateTrackInfo();
    });
    ctrls.push(c);
  }
}

function clearCtrls() {
  ctrls.forEach(c => c.marker && c.marker.setMap(null));
  ctrls = [];
}

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
  if (lastFit) renderSummary(lastFit.summary, m);
  $("runarea-info").innerHTML =
    `<span style="color:var(--warn)">轨迹已手动调整</span><br>` +
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
      `<span style="color:var(--red)">请先「拟合轨迹」生成轨迹后再调整。</span>`;
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
      `<span style="color:var(--red)">⚠ 实验性功能：手动调整尚在调试，` +
      `效果可能与预期不符，必要时需重新拟合轨迹，请谨慎使用。</span><br>` +
      `<span style="color:var(--warn)">拖动彩色圆点调整轨迹；` +
      `红色打卡点（${snapIdx.length} 个锚点）保持必过。</span>`;
  } else {
    clearCtrls();
    updateTrackInfo();
  }
};
