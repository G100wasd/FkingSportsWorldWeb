/* ---------- 全局状态 / 通用工具 ---------- */
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
// #status(顶栏) 与 #m-status(登录卡) 共用 .statusline 样式，setStatus 双写
let map = null, sess = null, AMap_ = null;
let locMarker = null, picking = false;
let overlays = [];
let fitOverlays = [];
let trackLocs = [];
let origLocs = [];
let lapIdx = [];
let snapIdx = [];
let trackSegs = [];
let ctrls = [];
let ctrlTotal = 8;
let ctrlWeight = 0.8;
let ctrlRadius = 12;
let editing = false;
let lastFit = null;
let FE = { run: {}, frontend: {} };

function setStatus(cls, text) {
  [["status", "status-txt"], ["m-status", "m-status-txt"]].forEach(([a, b]) => {
    const el = $(a), t = $(b);
    if (el) el.className = "statusline " + cls;
    if (t) t.textContent = text;
  });
}

// 左下状态块：off=未登录灰三角 // idle=待机金三角 // busy=作业中
function setRunBlock(state) {
  const b = $("map-block"), t = b.querySelector(".t");
  b.classList.toggle("off", state === "off");
  t.innerHTML = state === "off" ? "//&nbsp;OFFLINE"
              : state === "busy" ? "RUN&nbsp;//&nbsp;WORKING"
              : "RUN&nbsp;//&nbsp;STANDBY";
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

// 按钮结果闪烁：绿底 ✓成功 / 红底 ✗失败，1s 后恢复原样
function flashBtnResult(b, ok, resetText) {
  b.classList.remove("ok", "fail");
  b.classList.add(ok ? "ok" : "fail");
  b.textContent = ok ? "✓ 成功" : "✗ 失败";
  setTimeout(() => {
    b.classList.remove("ok", "fail");
    b.textContent = resetText;
    b.disabled = false;
  }, 1000);
}
