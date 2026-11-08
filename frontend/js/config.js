/* ---------- 跑步配置 ---------- */
let CFG_DEFAULT_DIST = 2200, CFG_DEFAULT_DUR = 720;
let CFG_START_LO = 6, CFG_START_HI = 22;
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
  // HH:MM:SS / MM:SS / 纯秒。注意：match 数组长度恒为"组数+1"，
  // 不能用它区分有无小时位——未命中的组是 undefined（曾导致 NaN，
  // 用时配置被静默忽略、全程回退默认值）
  const raw = halfWidth(($("cfg-dur").value || "").trim());
  const m = raw.match(/^(?:(\d+):)?(\d{1,3}):([0-5]\d)$/);
  if (m) return (+(m[1] || 0)) * 3600 + (+m[2]) * 60 + (+m[3]);
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
// 日期/伪装时间决定提交按钮是"提交记录"还是"延迟提交"
$("cfg-start").addEventListener("input", () => {
  startCache = null;
  updateCfgHint();
  updateSubmitMode();
});
$("cfg-date").addEventListener("input", () => {
  startCache = null;
  updateCfgHint();
  updateSubmitMode();
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
  const sec = Math.floor(Math.random() * 60);

  // 日期/拉取时间为会话值不落盘：留空=本地当前日期/时刻。
  // 合法 HH:MM 一律生效（窗口外只给警告，不静默丢弃——否则
  // "未来时间"判定会被悄悄抹平）。结束在未来合法：定时队列
  // 会等到 end+delay 再提交。
  const base = dv ? new Date(+dv[1], +dv[2] - 1, +dv[3]) : new Date();
  if (m) base.setHours(+m[1], +m[2], sec, 0);
  const ms = base.getTime();
  const warn = (m && ms + durMs > Date.now())
    ? "所选时刻的结束在当前之后，将排队至结束后自动提交" : null;
  return { ms, warn, explicit: !!m,
           outWin: !!m && (+m[1] < CFG_START_LO || +m[1] >= CFG_START_HI) };
}

function updateCfgHint() {
  const { ms, warn } = resolveStart(), d = new Date(ms);
  const p = n => String(n).padStart(2, "0");
  $("cfg-hint").innerHTML =
    (warn ? `<span style="color:var(--warn)">⚠ ${warn}</span><br>` : "") +
    `拉取：${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}` +
    ` ｜ ${cfgDist().toFixed(0)}m · ${fmtDur(cfgEffDur())} · ` +
    `${(cfgDist() / cfgEffDur()).toFixed(2)} m/s`;
  syncCadStride("stride");   // 速度变化后步频按已持久化的步幅重算
}

/* ---------- 保存前的配置检查 ----------
   红字（格式错误）→ 保存按钮下方说明区，且本次不落盘；
   黄字（语义警告）→ 对应输入框下方的 field-hint。 */
const FIELD_HINTS = ["cfg-start-hint", "cfg-dist-hint"];
function clearCfgHints() {
  FIELD_HINTS.forEach(id => { const e = $(id); if (e) e.textContent = ""; });
}

function cfgCheck() {
  clearCfgHints();
  const errs = [], num = s => halfWidth((s || "").trim());
  const v = id => num($(id).value);
  // 1) 格式：时间类必须是 时:分 / 分:秒，数值类必须是正数
  const start = v("cfg-start");
  if (start && !/^([01]?\d|2[0-3]):[0-5]\d$/.test(start))
    errs.push("拉取时间格式应为 HH:MM（如 07:30）");
  const dur = v("cfg-dur");
  if (dur && !/^(?:\d+:)?\d{1,3}:[0-5]\d$/.test(dur))
    errs.push("用时格式应为 mm:ss 或 hh:mm:ss（如 12:27）");
  const date = ($("cfg-date").value || "").trim();
  if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date))
    errs.push("日期格式不正确");
  [["cfg-dist", "距离"], ["cfg-speed", "均速"], ["cfg-alt", "最大海拔"],
   ["cfg-cad", "步频"], ["cfg-stride", "步幅"], ["cfg-cadmax", "最大步频"]]
    .forEach(([id, name]) => {
      const raw = v(id);
      if (raw && !(parseFloat(raw) > 0 && /^\d*\.?\d+$/.test(raw)))
        errs.push(`${name}应为正数`);
    });
  if (errs.length) return { errs };

  // 2) 语义警告（不阻断保存）
  const st = computeStart(), ms = st.ms, now = Date.now();
  let future = 0;
  const warns = [];
  if (st.outWin)
    warns.push(`该时刻在校方允许的 ${CFG_START_LO}:00-${CFG_START_HI}:00 窗口外`);
  if (start) {
    if (ms > now + 30000) {
      future = ms;
    } else if (now - ms > 120000) {
      warns.push(`拉取时间过远（${Math.round((now - ms) / 60000)} 分钟前），` +
                 `可能导致服务器日志对不上`);
    }
  }
  if (warns.length) $("cfg-start-hint").textContent = warns.join("；");
  if (cfgDist() < 2000)
    $("cfg-dist-hint").textContent =
      `里程 ${cfgDist().toFixed(0)}m 可能不够（多数学校计分跑要求 ≥2000m）`;
  return { errs: [], future };
}
function fitGoal() {
  return { dist: cfgDist(), dur: cfgEffDur(), start_ms: resolveStartMs() };
}

/* 保存按钮：当前输入值持久化到服务端 defaultsConfig.json 的 run/track 段；
   拟合/提交链路随后以 JSON 落盘值为准（boot 时全量回填各输入框）。 */
$("btn-cfg-save").onclick = async () => {
  const btn = $("btn-cfg-save");
  const chk = cfgCheck();
  if (chk.errs.length) {        // 格式错误：红字报错，本次不落盘
    flashBtnResult(btn, false, "保存配置");
    $("cfg-hint").innerHTML =
      `<span style="color:var(--red)">配置未保存：` +
      chk.errs.map(esc).join("；") + `</span>`;
    return;
  }
  btn.disabled = true;
  try {
    const payload = {
      default_dist: cfgDist(),
      default_dur: cfgEffDur(),
    };
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
    if (r.run.start_window) [CFG_START_LO, CFG_START_HI] = r.run.start_window;
    if (r.track?.alt_cap) $("cfg-alt").value = r.track.alt_cap;
    if (r.track?.cad_max) $("cfg-cadmax").value = r.track.cad_max;
    if (r.track?.stride_mean) $("cfg-stride").value = r.track.stride_mean;
    flashBtnResult(btn, true, "保存配置");
    updateCfgHint();
    updateSubmitMode();
    if (chk.future) {
      // 拉取时间在未来：此刻拉不了点（点位是实时下发的），
      // 只能排一个定时任务——直接把用户送到提交卡
      const t = new Date(chk.future);
      const p = n => String(n).padStart(2, "0");
      $("submit-defer-hint").innerHTML =
        `拉取时间设在 ${p(t.getHours())}:${p(t.getMinutes())}（未来）：` +
        `当前无法拉取打卡点，需创建定时任务——点"延迟提交"，` +
        `到点后自动 拉点 → 拟合 → 跑完 → 提交`;
      gotoCard("c-result");
      return;
    }
    $("submit-defer-hint").textContent = "";
    // 已有拟合结果 → 按新配置重算记录数据（跑道几何在服务端缓存，
    // 不重新拟合、不重拉点）；服务端已作废旧提交载荷，必须刷新，
    // 否则会提交与配置不符的记录
    if (lastFit && lastFit.locs && lastFit.locs.length) await recalc();
  } catch (e) {
    flashBtnResult(btn, false, "保存配置");
    $("cfg-hint").innerHTML =
      `<span style="color:var(--red)">保存失败：${esc(e.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
};
