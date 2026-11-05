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
}
function fitGoal() {
  return { dist: cfgDist(), dur: cfgEffDur(), start_ms: resolveStartMs() };
}
