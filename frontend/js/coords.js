/* ---------- BD-09 <-> GCJ-02 ---------- */
const X_PI = Math.PI * 3000.0 / 180.0;
function bd09ToGcj02(lat, lon) {
  const x = lon - 0.0065, y = lat - 0.006;
  const z = Math.sqrt(x * x + y * y) - 0.00002 * Math.sin(y * X_PI);
  const t = Math.atan2(y, x) - 0.000003 * Math.cos(x * X_PI);
  return [z * Math.sin(t), z * Math.cos(t)];
}
function gcj02ToBd09(lat, lon) {
  const x = lon, y = lat;
  const z = Math.sqrt(x * x + y * y) + 0.00002 * Math.sin(y * X_PI);
  const t = Math.atan2(y, x) + 0.000003 * Math.cos(x * X_PI);
  return [z * Math.sin(t) + 0.006, z * Math.cos(t) + 0.0065];
}

/* ---------- 定位输入框 ---------- */
function readInputs() {
  const lat = parseFloat($("inp-lat").value);
  const lon = parseFloat($("inp-lon").value);
  if (!isFinite(lat) || !isFinite(lon)) return null;
  return [lat, lon];
}
function writeInputs(gLat, gLon) {
  $("inp-lat").value = gLat.toFixed(7);
  $("inp-lon").value = gLon.toFixed(7);
}
function bdToLngLat(bdLat, bdLon) {
  const [gLat, gLon] = bd09ToGcj02(bdLat, bdLon);
  return new AMap_.LngLat(gLon, gLat);
}
function setMarkerAt(gLat, gLon, label) {
  const ll = new AMap_.LngLat(gLon, gLat);
  if (locMarker) {
    locMarker.setPosition(ll);
    if (label) locMarker.setLabel({ content: label, direction: "top" });
  } else {
    locMarker = new AMap_.Marker({
      position: ll, map, anchor: "bottom-center",
      label: { content: label || "锚点", direction: "top" },
    });
  }
  return ll;
}
function syncMarkerFromInputs() {
  const c = readInputs();
  if (c && map) setMarkerAt(c[0], c[1]);
}
