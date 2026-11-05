/* ---------- 全局状态 / 通用工具 ---------- */
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
const statusEl = $("status"), statusText = $("status-txt");
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
  statusEl.className = cls;
  statusText.textContent = text;
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
