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

// 顶栏与登录卡两套控件：凭据镜像同步，按钮共用同一登录链
const compact = () => matchMedia("(max-width:768px)").matches;
function creds() {
  const u = $("inp-user").value.trim() || $("m-user").value.trim();
  const p = $("inp-pass").value || $("m-pass").value;
  return { u, p };
}
function setAuthState(logged) {
  ["btn-auth", "m-auth"].forEach(id => $(id).textContent = logged ? "登出" : "登录");
}
function setAuthBusy(b) {
  ["btn-auth", "m-auth"].forEach(id => $(id).disabled = b);
}
function gotoCard(id) {
  const ic = document.querySelector(`.rail-ico[data-card="${id}"]`);
  if (ic) goTo(ic);
}

function renderLoginInfo() {
  const el = $("login-info"); if (!el) return;
  const plat = (sess && sess.platform === "ios") ? "iOS" : "Android";
  const fake = !DEVICE.detected;   // 桌面伪装成 Android
  const cells = [
    ["环境", DEVICE.detected ? "手机" : "电脑"],
    [fake ? "系统·伪装" : "系统", plat + " " + ((sess && sess.os_version) || DEVICE.os_version)],
    [fake ? "设备·伪装" : "设备", (sess && sess.device_name) || DEVICE.device_name],
    ["账号", sess ? (sess.name || sess.uid || "已登录") : "未登录"],
    ["锚点", sess && sess.anchor ? "已保存" : "—"],
    ["回执", sess && sess.has_receipt ? "已提交" : "—"],
  ];
  el.innerHTML = cells.map(([k, v]) =>
    `<div class="kv"><b>${esc(String(v))}</b><span>${k}</span></div>`).join("");
}

async function doLogin() {
  const { u: username, p: password } = creds();
  if (!username || !password) { setStatus("err", "请输入账号密码"); return; }
  setAuthBusy(true);
  setStatus("busy", "登录中...");
  try {
    sess = await api("/api/login", { username, password, device: DEVICE });
    const env = sess.platform
      ? `${sess.platform === "android" ? "Android" : "iOS"} ` +
        `${sess.os_version} ${sess.device_name}`
      : deviceLabel();
    setStatus("on", `已登录 ${sess.name || ""} (uid=${sess.uid}) ｜ ${env}`);
    setAuthState(true);
    renderLoginInfo();
    setRunBlock("idle");
    if (!map) await initMap().catch(e => setStatus("err", "地图加载失败: " + e));
    else {
      // 登出时遮罩(map-emblem)被重新显示而 map 实例仍在：
      // 跳过 initMap 分支需手动关遮罩并刷新尺寸，否则地图被占位图盖住
      $("map-emblem").style.display = "none";
      map.resize();
    }
    if (!applyAnchor(sess.anchor) && map) locateMe(AMap_);
    updateAutoBtn();
    if (compact()) gotoCard("c-anchor");   // 手机端登录后跳回定位卡
    if (AUTO_MODE) runAutoFlow();      // ?auto 链接：登录成功即接自动链
  } catch (e) {
    setStatus("err", e.message);
  } finally {
    setAuthBusy(false);
  }
}

async function doLogout() {
  await api("/api/logout").catch(() => {});
  sess = null;
  setAuthState(false);
  ["btn-auto", "m-auto"].forEach(id => {
    const ab = $(id);
    ab.disabled = true;
    ab.classList.remove("ok", "fail");
    ab.textContent = "自动跑";
  });
  renderLoginInfo();
  $("map-emblem").style.display = "";
  const w = $("submit-warn");
  if (w) w.textContent = "";
  setRunBlock("off");
  setStatus("off", "未登录");
}

$("btn-auth").onclick = () => { sess ? doLogout() : doLogin(); };
$("m-auth").onclick = () => { sess ? doLogout() : doLogin(); };
$("inp-pass").addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });
$("m-pass").addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });
// 两套输入框镜像
[["inp-user", "m-user"], ["inp-pass", "m-pass"]].forEach(([a, b]) => {
  $(a).addEventListener("input", e => $(b).value = e.target.value);
  $(b).addEventListener("input", e => $(a).value = e.target.value);
});

/* ---------- 自动跑：锚点+回执双前置，一键 拉点->拟合->提交 ---------- */
/* ?auto 链接模式：打开后手动登录，登录/会话恢复成功即自动跑；
   可用 ?lat=&lon= 覆盖锚点，?dist=&dur= 覆盖目标 */
const AUTO_Q = new URLSearchParams(location.search);
const AUTO_MODE = AUTO_Q.has("auto");

function updateAutoBtn() {
  const ok = !!(sess && sess.anchor && sess.has_receipt);
  ["btn-auto", "m-auto"].forEach(id => {
    const b = $(id);
    b.disabled = !ok;
    b.title = ok ? "一键：拉打卡点 → 拟合 → 提交"
                 : "需要该账号有保存的锚点且成功提交过一次";
  });
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

async function runAutoFlow(btn) {
  if (!sess) return;
  const lat = parseFloat(AUTO_Q.get("lat")), lon = parseFloat(AUTO_Q.get("lon"));
  const a = (isFinite(lat) && isFinite(lon)) ? { lat, lon } : sess.anchor;
  const b = btn || $("btn-auto");
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
    // 拟合可能无可行解：与手动 doFit 同策略——重新拉点重试，
    // 拉取达 MAX_REFIT 次仍失败才报红字
    for (let attempt = 1; attempt <= MAX_REFIT; attempt++) {
      setStatus("busy", `自动流程：拉取打卡点` +
        (attempt > 1 ? `（第 ${attempt}/${MAX_REFIT} 次）` : "…"));
      await api("/api/points", { lat: +a.lat, lon: +a.lon });
      setStatus("busy", "自动流程：拟合轨迹…");
      try {
        await api("/api/fit", autoGoal());
        break;
      } catch (e) {
        if (!e.message.includes("无可行解") || attempt >= MAX_REFIT) throw e;
        setStatus("busy", `拟合无可行解，重新拉点重试…`);
      }
    }
    setStatus("busy", "自动流程：提交记录…");
    const r = await api("/api/submit", {});
    if (r.scheduled) {
      flashBtnResult(b, true, "自动跑");
      setStatus("on", `自动跑已排队，约 ${Math.max(1, Math.round(r.eta_s / 60))} 分钟后自动提交`);
      $("runarea-info").innerHTML =
        `<span style="color:var(--warn)">自动流程完成：已加入定时提交队列，到点自动提交</span>`;
      watchQueue(r.uuid, r.fire_at);
      return;
    }
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

$("btn-auto").onclick = e => runAutoFlow(e.currentTarget);
$("m-auto").onclick = e => runAutoFlow(e.currentTarget);
renderLoginInfo();

$("btn-manual").onclick = () => {
  if (!DEVICE.detected) {
    alert("当前为 PC 环境。\n「手动跑步」仅支持手机环境使用，请在手机浏览器中打开本页面。");
    return;
  }
  $("manual-hint").textContent =
    `环境检测通过：${DEVICE.platform === "android" ? "Android" : "iOS"} ` +
    `${DEVICE.os_version} ${DEVICE.device_name} — 手动跑步功能开发中。`;
};
