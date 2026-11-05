/* ---------- 高德地图 ---------- */
let _loaderP = null;
function ensureLoader() {
  if (!_loaderP) {
    window._AMapSecurityConfig = { securityJsCode: FE.frontend.amap_security };
    _loaderP = new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = "https://webapi.amap.com/loader.js";
      s.onload = res;
      s.onerror = () => rej(new Error("高德 loader 加载失败"));
      document.head.appendChild(s);
    });
  }
  return _loaderP;
}

function initMap() {
  return ensureLoader().then(() => AMapLoader.load({
    key: FE.frontend.amap_key,
    version: "2.0",
    plugins: ["AMap.Geolocation", "AMap.Scale", "AMap.ToolBar", "AMap.ConvertFrom"],
  })).then(AMap => {
    AMap_ = AMap;
    map = new AMap.Map("map", {
      zoom: 15, dragEnable: true, zoomEnable: true,
    });
    map.addControl(new AMap.Scale());
    map.addControl(new AMap.ToolBar({ position: "RB" }));
    map.on("click", onMapClick);
    $("map-emblem").style.display = "none";
    const c = readInputs();
    if (c) map.setZoomAndCenter(16, setMarkerAt(c[0], c[1]));
    locateMe(AMap);
  });
}

function onMapClick(e) {
  if (!picking) return;
  const gLat = e.lnglat.getLat(), gLon = e.lnglat.getLng();
  writeInputs(gLat, gLon);
  map.setCenter(setMarkerAt(gLat, gLon, "选点"));
  togglePick(false);
}

function locateMe(AMap, force) {
  const fill = (gLat, gLon, label) => {
    if (!force && readInputs()) return;
    writeInputs(gLat, gLon);
    map.setCenter(setMarkerAt(gLat, gLon, label));
  };
  const geo = new AMap.Geolocation({
    enableHighAccuracy: true, timeout: 10000,
    convert: true, showButton: false, showMarker: false,
  });
  geo.getCurrentPosition((status, result) => {
    if (status === "complete") {
      fill(result.position.lat, result.position.lng, "本机定位");
    } else {
      navigator.geolocation?.getCurrentPosition(
        pos => {
          const ll = [pos.coords.longitude, pos.coords.latitude];
          AMap.convertFrom(ll, "gps", (st, res) => {
            const g = (st === "ok" && res.locations[0]) ? res.locations[0] : ll;
            fill(g.lat, g.lng, "GPS定位");
          });
        },
        () => {},
        { enableHighAccuracy: true, timeout: 8000 },
      );
    }
  });
}

/* ---------- 面板按钮 ---------- */
function togglePick(force) {
  picking = (force !== undefined) ? force : !picking;
  $("btn-pick").classList.toggle("active", picking);
  $("btn-pick").textContent = picking ? "点击地图选点…" : "地图选点";
  $("map").classList.toggle("picking", picking);
}

function applyAnchor(a) {
  if (a && isFinite(+a.lat) && isFinite(+a.lon)) {
    writeInputs(+a.lat, +a.lon);
    if (map) map.setCenter(setMarkerAt(+a.lat, +a.lon, "上次锚点"));
    return true;
  }
  return false;
}

$("btn-pick").onclick = () => togglePick();
$("btn-geoloc").onclick = () => { if (AMap_) locateMe(AMap_, true); };
$("btn-goto").onclick = () => {
  const c = readInputs();
  if (!c) return;
  map.setZoomAndCenter(Math.max(map.getZoom(), 16), setMarkerAt(c[0], c[1], "锚点"));
};
$("inp-lat").addEventListener("input", syncMarkerFromInputs);
$("inp-lon").addEventListener("input", syncMarkerFromInputs);
