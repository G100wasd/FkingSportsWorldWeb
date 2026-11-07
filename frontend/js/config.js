/* ---------- 跑步配置 ---------- */
let CFG_DEFAULT_DIST = 2200, CFG_DEFAULT_DUR = 720;
let CFG_START_LO = 6, CFG_START_HI = 22;
let CFG_DEF_START = "15:30";
let CFG_JITTER = [1, 5];
let TAPER_PTS = 14;
let SAMPLE_ROWS = 40;

function cfgDist() {
  const v = parseFloat($("cfg-dist").value);
  return isFinite(v) && v > 0 ? v : CFG_DEFAULT_DIST;
}
function cfgSpeed() {
  const v = parseFloat($("cfg-speed").value);
  return isFinite(v) && v > 0 ? v : null;
}
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
function cfgEffDur() {
  const d = cfgDur(), sp = cfgSpeed();
  return d || (sp ? Math.round(cfgDist() / sp) : CFG_DEFAULT_DUR);
}
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
/* 步频×步幅÷60=均速：给定卡片速度，两者只独立其一。
   持久化的是步幅(stride_mean)，步频为联动显示/输入入口。 */
function syncCadStride(src) {
  const v = cfgDist() / cfgEffDur();
  if (!(v > 0)) return;
  const cad = parseFloat(halfWidth(($("cfg-cad").value || "").trim()));
  const str = parseFloat(halfWidth(($("cfg-stride").value || "").trim()));
  if (src === "cad" && Number.isFinite(cad) && cad > 0)
    $("cfg-stride").value = (v * 60.0 / cad).toFixed(2);
  else if (src === "stride" && Number.isFinite(str) && str > 0)
    $("cfg-cad").value = (v * 60.0 / str).toFixed(0);
}
$("cfg-cad").addEventListener("input", () => syncCadStride("cad"));
$("cfg-stride").addEventListener("input", () => syncCadStride("stride"));
$("cfg-start").addEventListener("input", () => {
  startCache = null;
  updateCfgHint();
});
$("cfg-date").addEventListener("input", () => {
  startCache = null;
  updateCfgHint();
});

let startCache = null;
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
  if (ms + durMs > Date.now()) {
    warn = "所选起跑时间尚未发生，已自动调整为当天日期";
    const now = new Date();
    $("cfg-date").value =
      `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
    base = new Date();
    applyTime(base);
    ms = base.getTime();
    if (ms + durMs > Date.now()) ms -= 86400000;
  }
  return { ms, warn };
}

function updateCfgHint() {
  const { ms, warn } = resolveStart(), d = new Date(ms);
  const p = n => String(n).padStart(2, "0");
  $("cfg-hint").innerHTML =
    (warn ? `<span style="color:var(--warn)">⚠ ${warn}</span><br>` : "") +
    `起跑：${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}` +
    ` ｜ ${cfgDist().toFixed(0)}m · ${fmtDur(cfgEffDur())} · ` +
    `${(cfgDist() / cfgEffDur()).toFixed(2)} m/s`;
  syncCadStride("stride");   // 速度变化后步频按已持久化的步幅重算
}
function fitGoal() {
  return { dist: cfgDist(), dur: cfgEffDur(), start_ms: resolveStartMs() };
}

/* 保存按钮：当前输入值持久化到服务端 projectConfig.json 的 run 段；
   拟合/提交链路随后以 JSON 落盘值为准（boot 时全量回填各输入框）。 */
$("btn-cfg-save").onclick = async () => {
  const btn = $("btn-cfg-save");
  btn.disabled = true;
  try {
    const startRaw = halfWidth(($("cfg-start").value || "").trim());
    const payload = {
      default_dist: cfgDist(),
      default_dur: cfgEffDur(),
    };
    if (/^([01]?\d|2[0-3]):[0-5]\d$/.test(startRaw))
      payload.default_start = startRaw.padStart(5, "0");
    const altRaw = parseFloat(halfWidth(($("cfg-alt").value || "").trim()));
    if (Number.isFinite(altRaw) && altRaw > 0) payload.alt_cap = altRaw;
    const cadRaw = parseFloat(halfWidth(($("cfg-cadmax").value || "").trim()));
    if (Number.isFinite(cadRaw) && cadRaw > 0) payload.cad_max = cadRaw;
    let strRaw = parseFloat(halfWidth(($("cfg-stride").value || "").trim()));
    if (!(Number.isFinite(strRaw) && strRaw > 0)) {
      // 步幅留空但步频有效 → 由 v=cad·str/60 反推步幅入库
      const cadIn = parseFloat(halfWidth(($("cfg-cad").value || "").trim()));
      const v = cfgDist() / cfgEffDur();
      if (Number.isFinite(cadIn) && cadIn > 0 && v > 0)
        strRaw = v * 60.0 / cadIn;
    }
    if (Number.isFinite(strRaw) && strRaw > 0) payload.stride_mean = strRaw;
    const r = await api("/api/config", payload);
    CFG_DEFAULT_DIST = r.run.default_dist ?? CFG_DEFAULT_DIST;
    CFG_DEFAULT_DUR = r.run.default_dur ?? CFG_DEFAULT_DUR;
    CFG_DEF_START = r.run.default_start || CFG_DEF_START;
    if (r.run.start_jitter_min) CFG_JITTER = r.run.start_jitter_min;
    if (r.run.start_window) [CFG_START_LO, CFG_START_HI] = r.run.start_window;
    if (r.track?.alt_cap) $("cfg-alt").value = r.track.alt_cap;
    if (r.track?.cad_max) $("cfg-cadmax").value = r.track.cad_max;
    if (r.track?.stride_mean) $("cfg-stride").value = r.track.stride_mean;
    flashBtnResult(btn, true, "保存配置");
    updateCfgHint();
    // 已有拟合结果 → 用新配置自动重算，让拟合卡同步最新配置
    // （服务端已同时作废旧提交载荷，不刷新会提交与配置不符的记录）
    if (lastFit && lastFit.locs && lastFit.locs.length) {
      $("cfg-hint").innerHTML +=
        `<br><span style="color:var(--warn)">配置已更新，重新拟合中…</span>`;
      // 拟合结束刷新 hint 会整体重写，黄字自然消失（失败信息在区域卡显示）
      doFit().finally(() => updateCfgHint());
    }
  } catch (e) {
    flashBtnResult(btn, false, "保存配置");
    $("cfg-hint").innerHTML =
      `<span style="color:var(--red)">保存失败：${esc(e.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
};
