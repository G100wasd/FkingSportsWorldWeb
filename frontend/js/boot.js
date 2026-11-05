/* ---------- 启动 ---------- */
(async function boot() {
  try {
    const d = await fetch("/api/defaults").then(r => r.json());
    FE = d;
    if (d.username) { $("inp-user").value = d.username; $("m-user").value = d.username; }
    if (d.password) { $("inp-pass").value = d.password; $("m-pass").value = d.password; }
    const R = d.run || {}, F = d.frontend || {}, C = F.ctrl || {};
    CFG_DEFAULT_DIST = R.default_dist ?? CFG_DEFAULT_DIST;
    CFG_DEFAULT_DUR = R.default_dur ?? CFG_DEFAULT_DUR;
    CFG_DEF_START = R.default_start || CFG_DEF_START;
    CFG_JITTER = R.start_jitter_min || CFG_JITTER;
    if (R.start_window) [CFG_START_LO, CFG_START_HI] = R.start_window;
    MAX_REFIT = F.max_refit ?? MAX_REFIT;
    LAP_COLORS = F.lap_colors || LAP_COLORS;
    TAPER_PTS = F.taper_points ?? TAPER_PTS;
    SAMPLE_ROWS = F.sample_rows ?? SAMPLE_ROWS;
    ctrlTotal = C.total ?? ctrlTotal;
    ctrlWeight = C.weight ?? ctrlWeight;
    ctrlRadius = C.radius ?? ctrlRadius;
    $("cfg-dist").value = CFG_DEFAULT_DIST;
    $("cfg-dur").placeholder = fmtDur(CFG_DEFAULT_DUR) + "（默认）";
    $("ctrl-count").max = C.max || 10;
    $("ctrl-count").value = ctrlTotal;
    $("ctrl-count-val").textContent = ctrlTotal;
    $("ctrl-weight").value = ctrlWeight;
    $("ctrl-weight-val").textContent = ctrlWeight;
    $("ctrl-radius").value = ctrlRadius;
    $("ctrl-radius-val").textContent = ctrlRadius + "m";
  } catch (e) {
    console.warn("配置下发失败，使用内置默认值", e);
  }
  updateCfgHint();

  try {
    const d = await fetch("/api/session").then(r => r.json());
    if (d.logged_in) {
      sess = d;
      const env = d.platform
        ? `${d.platform === "android" ? "Android" : "iOS"} ` +
          `${d.os_version} ${d.device_name}`
        : "";
      setStatus("on", `已登录 ${d.name || ""} (uid=${d.uid})` +
                (env ? ` ｜ ${env}` : ""));
      setAuthState(true);
      setRunBlock("idle");
      await initMap().catch(() => {});
      if (!applyAnchor(d.anchor) && map) locateMe(AMap_);
      updateAutoBtn();
      if (AUTO_MODE) runAutoFlow();    // ?auto 链接：会话有效直接开跑
    }
  } catch (e) { /* 忽略 */ }
  renderLoginInfo();
  // 手机端未登录：默认落登录卡
  if (!sess && compact()) gotoCard("c-login");
})();
