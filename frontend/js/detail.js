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

function renderDetail(d) {
  const f = d.fit || {}, full = d.locs_full || [];
  const snap = new Set(d.snap_idx || []);
  const tbl = (heads, rows) =>
    `<table><tr>${heads.map(h => `<th>${h}</th>`).join("")}</tr>` +
    rows.map(r => `<tr${r.snap ? ' class="snap"' : ""}>` +
      r.cells.map(c => `<td>${c}</td>`).join("") + `</tr>`).join("") +
    `</table>`;
  let html = "";

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

  html += `<div class="detail-sec"><h4>速度曲线（蓝=段均速avgSpeed · 灰点=GPS瞬时 · ` +
    `橙=海拔 · 红点=打卡 · 虚线=圈界）</h4>` +
    `<canvas id="speed-chart"></canvas></div>`;

  if (full.length) {
    const step = Math.max(1, Math.floor(full.length / SAMPLE_ROWS));
    const rows = [];
    for (let i = 0; i < full.length; i++) {
      if (i % step !== 0 && !snap.has(i) && i !== full.length - 1) continue;
      const q = full[i];
      rows.push({ snap: snap.has(i), cells: [
        i, fmtDur(q.totalTime || 0), (q.totalDis || 0).toFixed(1),
        q.steps ?? "-",
        (q.avgSpeed ?? "-") === "-" ? "-" : (+q.avgSpeed).toFixed(2),
        (q.speed ?? "-") === "-" ? "-" : (+q.speed / 3.6).toFixed(2),
        (q.bdA ?? "-") === "-" ? "-" : (+q.bdA).toFixed(1),
        q.type ?? q.ptype ?? "-", snap.has(i) ? "●" : "",
      ] });
    }
    html += `<div class="detail-sec"><h4>逐点采样（每 ${step} 点 + 全部打卡点）</h4>` +
      `<div class="detail-scroll">` +
      tbl(["#", "t", "里程m", "步数", "均速m/s", "GPS速m/s", "海拔m", "类型", "打卡"], rows) +
      `</div></div>`;
  }

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

  if (d.five_point) {
    let pts = [];
    try { pts = JSON.parse(d.five_point.fivePointJson || "[]"); } catch (e) {}
    const pass = pts.filter(p => p.isPass).length;
    const rows = pts.map((p, i) => ({ cells: [
      i + 1, p.pointName || "-",
      p.isPass ? '<b style="color:var(--green)">✓ 经过</b>' : '<b style="color:var(--red)">✗ 未过</b>',
      p.isFixed ?? "-",
      (+p.lat || 0).toFixed(6), (+p.lon || 0).toFixed(6),
    ] }));
    html += `<div class="detail-sec"><h4>五点打卡回执（${pass}/${pts.length} 通过｜` +
      `runAreaId=${d.five_point.runAreaId}｜围栏${d.five_point.freedomShowFence ? "有" : "无"}）</h4>` +
      tbl(["#", "点名", "isPass", "isFixed", "lat(BD)", "lon(BD)"], rows) +
      `</div>`;
  }

  if (d.record_body) {
    const b = d.record_body;
    const rows = [
      ["sportType", b.sportType], ["policy", b.policy],
      ["totalTime", b.totalTime], ["totalDis", b.totalDis],
      ["speed(千分位配速)", b.speed], ["validDis", b.validDis],
      ["validTime", b.validTime], ["totalSteps", b.totalSteps],
      ["avgStepFreq", b.avgStepFreq], ["calorie", b.calorie],
      ["avgPower", b.avgPower], ["totalAscent", b.totalAscent],
      ["selDistance", b.selDistance], ["startTime", b.startTime],
      ["stopTime", b.stopTime], ["faceCheck", b.faceCheck],
      ["uid", b.uid], ["selectedUnid", b.selectedUnid],
      ["uuid", b.uuid], ["address", b.address || "(空)"],
    ].map(([k, v]) => ({ cells: [k, v] }));
    html += `<div class="detail-sec"><h4>提交体预览（save/record，未发送）</h4>` +
      `<div class="detail-scroll">` + tbl(["字段", "值"], rows) + `</div>` +
      `<pre class="sign-block">signature: ${esc(b.signature)}\n` +
      `originalSign: ${esc(String(b.originalSign))}</pre></div>`;
  }

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

  if (Array.isArray(f.point_errors) && f.point_errors.length) {
    html += `<div class="detail-sec"><h4>各打卡点拟合误差（m）</h4>` +
      tbl(["#1", "#2", "#3", "#4", "#5", "#6"].slice(0, f.point_errors.length),
        [{ cells: f.point_errors.map(e => (+e).toFixed(2)) }]) + `</div>`;
  }
  $("fit-detail-view").innerHTML = html;
  drawSpeedChart(full, snap, d.locs);
}

function drawSpeedChart(full, snap, locs) {
  const cv = $("speed-chart");
  if (!cv || !full.length) return;
  const W = cv.clientWidth || 300, H = 120, pad = 6;
  const dpr = window.devicePixelRatio || 1;
  cv.width = W * dpr; cv.height = H * dpr;
  const ctx = cv.getContext("2d");
  ctx.scale(dpr, dpr);
  const avgs = full.map(q => +q.avgSpeed || 0);
  const gpss = full.map(q => (+q.speed || 0) / 3.6);
  const vmax = Math.max(...avgs, 0.1), vmin = Math.min(...avgs);
  const X = i => pad + i * (W - 2 * pad) / (full.length - 1);
  const Y = v => H - pad - (v - vmin) * (H - 2 * pad) / (vmax - vmin || 1);
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
  const alts = full.map(q => +q.bdA || 0);
  const amax = Math.max(...alts, 0.1), amin = Math.min(...alts);
  const YA = v => H - pad - (v - amin) * (H - 2 * pad) / (amax - amin || 1);
  ctx.strokeStyle = "#dd6b20"; ctx.lineWidth = 0.8; ctx.globalAlpha = 0.7;
  ctx.beginPath();
  alts.forEach((v, i) => i ? ctx.lineTo(X(i), YA(v)) : ctx.moveTo(X(i), YA(v)));
  ctx.stroke();
  ctx.globalAlpha = 1;
  ctx.fillStyle = "rgba(160,174,192,0.55)";
  gpss.forEach((v, i) => { ctx.beginPath(); ctx.arc(X(i), Y(v), 1.4, 0, 7); ctx.fill(); });
  ctx.strokeStyle = "#0e9fb5"; ctx.lineWidth = 1.2; ctx.beginPath();
  avgs.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v)));
  ctx.stroke();
  ctx.fillStyle = "#d64545";
  snap.forEach(i => {
    if (i < avgs.length) { ctx.beginPath(); ctx.arc(X(i), Y(avgs[i]), 3, 0, 7); ctx.fill(); }
  });
  ctx.fillStyle = "#96a0ab"; ctx.font = "10px sans-serif";
  ctx.fillText(vmax.toFixed(2) + " m/s", pad + 2, pad + 9);
  ctx.fillText(vmin.toFixed(2), pad + 2, H - pad - 2);
  ctx.fillStyle = "#dd6b20";
  ctx.fillText(amin.toFixed(1) + "~" + amax.toFixed(1) + " m", W - pad - 68, pad + 9);
}

function renderSummary(sum, adjustedMeters) {
  if (!sum) return;
  const dist = adjustedMeters != null ? adjustedMeters : sum.distance_m;
  const adj = adjustedMeters != null ? "~" : "";
  const kv = (v, l) => `<div class="kv"><b>${v}</b><span>${l}</span></div>`;
  let html =
    kv(`${adj}${(dist / 1000).toFixed(2)} km`, "DIST") +
    kv(fmtDur(sum.duration_s), "TIME") +
    kv(fmtPace(sum.pace_s_per_km) + "/km", "PACE") +
    kv(`${sum.avg_speed_mps} m/s`, "AVG SPD") +
    kv(sum.total_steps ?? "-", "STEPS") +
    kv(`${sum.step_freq ?? "-"} spm`, "CADENCE") +
    kv(sum.max_step_freq != null ? `${sum.max_step_freq} spm` : "-", "MAX CAD") +
    kv(sum.max_stride != null ? `${sum.max_stride} m` : "-", "MAX STR") +
    kv(`${sum.calorie ?? "-"} kcal`, "CAL") +
    kv(`${sum.avg_power ?? "-"} W`, "POWER") +
    kv(`${sum.ascent_m ?? "-"} m`, "ASCENT") +
    kv(sum.valid_dis_m != null ? (sum.valid_dis_m / 1000).toFixed(2) + " km" : "-", "VALID") +
    kv(sum.point_count, "POINTS") +
    kv(`${sum.snapped}/${sum.point_count ? 5 : 0}`, "SNAP");
  if (sum.start_ms) {
    const d = new Date(sum.start_ms);
    const p = n => String(n).padStart(2, "0");
    html += kv(`${p(d.getMonth() + 1)}-${p(d.getDate())}`, "DATE") +
            kv(`${p(d.getHours())}:${p(d.getMinutes())}`, "START");
  }
  $("fit-summary").innerHTML = `<div class="kv-grid">${html}</div>`;
}
