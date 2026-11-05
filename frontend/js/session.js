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
  $("map-emblem").style.display = "";
  setRunBlock("off");
  setStatus("off", "未登录");
}

$("btn-auth").onclick = () => { sess ? doLogout() : doLogin(); };
$("inp-pass").addEventListener("keydown", e => { if (e.key === "Enter") doLogin(); });

$("btn-manual").onclick = () => {
  if (!DEVICE.detected) {
    alert("当前为 PC 环境。\n「手动跑步」仅支持手机环境使用，请在手机浏览器中打开本页面。");
    return;
  }
  $("manual-hint").textContent =
    `环境检测通过：${DEVICE.platform === "android" ? "Android" : "iOS"} ` +
    `${DEVICE.os_version} ${DEVICE.device_name} — 手动跑步功能开发中。`;
};
