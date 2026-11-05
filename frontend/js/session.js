/* ---------- 设备环境探测 ---------- */
function detectDevice() {
  const ua = navigator.userAgent || "";
  if (/iPhone|iPad|iPod/i.test(ua)) {
    const m = ua.match(/OS (\d+([._]\d+)*)/);
    return { platform: "ios", device_name: "iPhone",
             os_version: m ? m[1].replace(/_/g, ".") : "26.5.2",
             detected: true };
  }
  const m = ua.match(/Android (\d+\.?\d*)/i);
  if (m) {
    const model = (ua.match(/;\s*([^;)]+?)\s+Build\//) || [])[1] || "";
    return { platform: "android",
             device_name: model || "22081212C",
             os_version: m[1], detected: true };
  }
  return { platform: "android", device_name: "22081212C",
           os_version: "14", detected: false };
}
const DEVICE = detectDevice();

function deviceLabel(sess_platform) {
  const p = sess_platform || DEVICE.platform;
  const nm = sess_platform ? "" : DEVICE.device_name;
  return (p === "android" ? "Android" : "iOS") + " " +
         (sess_platform || DEVICE.os_version) +
         (sess_platform ? "" : " " + nm) +
         (DEVICE.detected ? "（检测）" : "（桌面伪装）");
}

async function doLogin() {
  const username = $("inp-user").value.trim();
  const password = $("inp-pass").value;
  if (!username || !password) { setStatus("err", "请输入账号密码"); return; }
  $("btn-auth").disabled = true;
  setStatus("busy", "登录中...");
  try {
    sess = await api("/api/login", { username, password, device: DEVICE });
    const env = sess.platform
      ? `${sess.platform === "android" ? "Android" : "iOS"} ` +
        `${sess.os_version} ${sess.device_name}`
      : deviceLabel();
    setStatus("on", `已登录 ${sess.name || ""} (uid=${sess.uid}) ｜ ${env}`);
    $("btn-auth").textContent = "登出";
    setRunBlock("idle");
    if (!map) await initMap().catch(e => setStatus("err", "地图加载失败: " + e));
    if (!applyAnchor(sess.anchor) && map) locateMe(AMap_);
    updateAutoBtn();
    if (AUTO_MODE) runAutoFlow();      // ?auto 链接：登录成功即接自动链
  } catch (e) {
    setStatus("err", e.message);
  } finally {
    $("btn-auth").disabled = false;
  }
}

async function doLogout() {
  await api("/api/logout").catch(() => {});
  sess = null;
  $("btn-auth").textContent = "登录";
  const ab = $("btn-auto");
  ab.disabled = true;
  ab.classList.remove("ok", "fail");
  ab.textContent = "自动跑";
  $("map-emblem").style.display = "";
  setRunBlock("off");
  setStatus("off", "未登录");
}

$("btn-auth").onclick = () => { sess ? doLogout() : doLogin(); };
$("inp-pass").addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });

/* ---------- 自动跑：锚点+回执双前置，一键 拉点->拟合->提交 ---------- */
/* ?auto 链接模式：打开后手动登录，登录/会话恢复成功即自动跑；
   可用 ?lat=&lon= 覆盖锚点，?dist=&dur= 覆盖目标 */
const AUTO_Q = new URLSearchParams(location.search);
const AUTO_MODE = AUTO_Q.has("auto");

function updateAutoBtn() {
  const b = $("btn-auto");
  const ok = !!(sess && sess.anchor && sess.has_receipt);
  b.disabled = !ok;
  b.title = ok ? "一键：拉打卡点 → 拟合 → 提交"
               : "需要该账号有保存的锚点且成功提交过一次";
}

async function refreshSessionFlags() {
  try {
    const d = await fetch("/api/session").then(r => r.json());
    if (d.logged_in) { sess.anchor = d.anchor; sess.has_receipt = d.has_receipt; }
  } catch (e) { /* 忽略 */ }
  updateAutoBtn();
}

function autoGoal() {
  const g = fitGoal();
  const dist = parseFloat(AUTO_Q.get("dist")), dur = parseInt(AUTO_Q.get("dur"));
  if (isFinite(dist) && dist > 0) g.dist = dist;
  if (isFinite(dur) && dur > 0) g.dur = dur;
  return g;
}

async function runAutoFlow() {
  if (!sess) return;
  const lat = parseFloat(AUTO_Q.get("lat")), lon = parseFloat(AUTO_Q.get("lon"));
  const a = (isFinite(lat) && isFinite(lon)) ? { lat, lon } : sess.anchor;
  const b = $("btn-auto");
  if (!a) {
    flashBtnResult(b, false, "自动跑");
    setStatus("err", "无锚点：链接带 ?lat=&lon= 或该账号先拟合过一次");
    return;
  }
  b.disabled = true;
  b.classList.remove("ok", "fail");
  b.textContent = "执行中…";
  setRunBlock("busy");
  try {
    setStatus("busy", "自动流程：拉取打卡点…");
    await api("/api/points", { lat: +a.lat, lon: +a.lon });
    setStatus("busy", "自动流程：拟合轨迹…");
    await api("/api/fit", autoGoal());
    setStatus("busy", "自动流程：提交记录…");
    const r = await api("/api/submit", {});
    flashBtnResult(b, true, "自动跑");
    setStatus("on", `自动完成 rrid=${r.rrid}` +
      (r.obs_ok != null ? ` ｜ OBS ${r.obs_ok}/${r.obs_total}` : "") +
      (r.verified ? " ｜ 已回读" : ""));
    $("runarea-info").innerHTML =
      `<span style="color:var(--green)">自动流程完成：` +
      `rrid=${r.rrid} uuid=${r.uuid}${r.already ? "（此前已提交过）" : ""}</span>`;
  } catch (e) {
    flashBtnResult(b, false, "自动跑");
    setStatus("err", "自动流程失败: " + e.message);
    $("runarea-info").innerHTML =
      `<span style="color:var(--red)">自动流程失败：${esc(e.message)}</span>`;
  } finally {
    setRunBlock(sess ? "idle" : "off");
  }
}

$("btn-auto").onclick = runAutoFlow;

$("btn-manual").onclick = () => {
  if (!DEVICE.detected) {
    alert("当前为 PC 环境。\n「手动跑步」仅支持手机环境使用，请在手机浏览器中打开本页面。");
    return;
  }
  $("manual-hint").textContent =
    `环境检测通过：${DEVICE.platform === "android" ? "Android" : "iOS"} ` +
    `${DEVICE.os_version} ${DEVICE.device_name} — 手动跑步功能开发中。`;
};
